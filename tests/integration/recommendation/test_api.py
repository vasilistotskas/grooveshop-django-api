"""The two public endpoints on the wire: shape, gating, and the
impression/click rows they leave behind."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse
from kombu.exceptions import OperationalError
from rest_framework import status
from rest_framework.test import APIClient

from product.enum.relation import RelationType
from product.factories.product import ProductFactory
from product.models import ProductRelation
from recommendation.enum import EventKind, StrategyCode, Surface
from recommendation.models import RecommendationEvent
from recommendation.tasks import record_recommendation_event
from tenant.models import DeferredDispatch
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)
from tests.utils.store_settings import switch_store_settings

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _store_shows_suggestions():
    """The merchant has product suggestions switched on (they ship off)."""
    switch_store_settings(PRODUCT_SUGGESTIONS_ENABLED=True)


def _product(**kwargs):
    kwargs.setdefault("active", True)
    kwargs.setdefault("stock", 5)
    kwargs.setdefault("num_images", 0)
    kwargs.setdefault("num_reviews", 0)
    return ProductFactory(**kwargs)


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def curated_seed():
    seed = _product()
    curated = _product(category=seed.category)
    ProductRelation.objects.create(
        from_product=seed,
        to_product=curated,
        relation_type=RelationType.COMPLEMENTARY,
    )
    _product(category=seed.category)
    return seed, curated


class TestRecommendations:
    url = reverse("recommendation-list")

    def test_a_seed_is_required_for_a_product_surface(self, client):
        response = client.get(self.url, {"surface": Surface.PDP})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "seed" in response.data

    def test_limit_is_capped(self, client):
        seed = _product()
        response = client.get(self.url, {"seed": seed.id, "limit": 99})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_curated_first_with_its_reason_and_a_correlation_id(
        self, client, curated_seed
    ):
        seed, curated = curated_seed

        response = client.get(
            self.url, {"surface": Surface.PDP, "seed": seed.id}
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert data["surface"] == Surface.PDP
        first = data["items"][0]
        assert first["product"]["id"] == curated.id
        assert first["reason"] == {
            "strategy": StrategyCode.CURATED,
            "relation_type": RelationType.COMPLEMENTARY,
            "score": first["reason"]["score"],
        }
        assert seed.id not in [item["product"]["id"] for item in data["items"]]

        # The body names no impression: it is cached and shared, and the
        # beacon mints the showing's id. The read path writes nothing: the
        # impression is the client's to report once the strip is actually
        # shown, so a cached or never-scrolled-to response cannot inflate a
        # strategy's impression count.
        assert "impression_id" not in data
        assert not RecommendationEvent.objects.exists()

    def test_wire_shape_is_camel_case(self, client, curated_seed):
        seed, _ = curated_seed
        body = client.get(self.url, {"seed": seed.id}).json()
        assert set(body) == {"surface", "items"}
        assert "relationType" in body["items"][0]["reason"]

    def test_nothing_worth_showing_is_an_empty_list(self, client):
        seed = _product()

        response = client.get(self.url, {"seed": seed.id})

        assert response.status_code == status.HTTP_200_OK
        assert response.data["items"] == []
        assert not RecommendationEvent.objects.exists()

    def test_the_empty_cart_is_answered_without_a_seed(self, client):
        shown = {_product().id for _ in range(3)}

        response = client.get(self.url, {"surface": Surface.EMPTY_CART})

        assert response.status_code == status.HTTP_200_OK
        items = response.data["items"]
        assert shown <= {item["product"]["id"] for item in items}
        assert {item["reason"]["strategy"] for item in items} == {
            StrategyCode.POPULAR
        }

    def test_exclusions_are_honoured(self, client, curated_seed):
        seed, curated = curated_seed
        response = client.get(
            self.url, {"seed": seed.id, "exclude": str(curated.id)}
        )
        assert curated.id not in [
            item["product"]["id"] for item in response.data["items"]
        ]


class TestPlanGate:
    url = reverse("recommendation-list")

    @pytest.fixture
    def gated_tenant(self, request):
        tenant = store_tenant(
            f"recs_gate_{request.param}",
            recommendations_enabled=request.param == "on",
        )
        previous = bind_store_tenant(tenant)
        yield tenant
        unbind_store_tenant(previous)

    @pytest.mark.parametrize("gated_tenant", ["off"], indirect=True)
    def test_flag_off_is_a_404_on_both_endpoints(self, client, gated_tenant):
        seed = _product()
        assert (
            client.get(self.url, {"seed": seed.id}).status_code
            == status.HTTP_404_NOT_FOUND
        )
        assert (
            client.post(
                reverse("recommendation-event"), {}, format="json"
            ).status_code
            == status.HTTP_404_NOT_FOUND
        )

    @pytest.mark.parametrize("gated_tenant", ["on"], indirect=True)
    def test_flag_on_serves(self, client, gated_tenant):
        seed = _product()
        assert (
            client.get(self.url, {"seed": seed.id}).status_code
            == status.HTTP_200_OK
        )

    @pytest.mark.parametrize("gated_tenant", ["on"], indirect=True)
    def test_the_store_switch_off_is_a_404_on_both_endpoints(
        self, client, gated_tenant
    ):
        switch_store_settings(PRODUCT_SUGGESTIONS_ENABLED=False)
        seed = _product()
        assert (
            client.get(self.url, {"seed": seed.id}).status_code
            == status.HTTP_404_NOT_FOUND
        )
        assert (
            client.post(
                reverse("recommendation-event"), {}, format="json"
            ).status_code
            == status.HTTP_404_NOT_FOUND
        )


class TestEvents:
    url = reverse("recommendation-event")

    def _payload(self, product, **overrides):
        payload = {
            "impressionId": str(uuid.uuid4()),
            "surface": Surface.PDP,
            "kind": EventKind.CLICK,
            "items": [
                {
                    "productId": product.id,
                    "strategy": StrategyCode.CURATED,
                    "position": 1,
                }
            ],
        }
        payload.update(overrides)
        return payload

    def test_a_click_is_accepted_and_recorded(self, client):
        product = _product()
        payload = self._payload(product)

        response = client.post(self.url, payload, format="json")

        assert response.status_code == status.HTTP_202_ACCEPTED
        row = RecommendationEvent.objects.get()
        assert row.kind == EventKind.CLICK
        assert row.product_id == product.id
        assert str(row.impression_id) == payload["impressionId"]
        assert row.position == 1

    def test_an_impression_is_recorded_under_the_id_its_response_names(
        self, client
    ):
        """The beacon is the first request that is one viewer's and passes
        no cache, so the showing's id is minted there."""
        payload = self._payload(_product(), kind=EventKind.IMPRESSION)
        del payload["impressionId"]

        response = client.post(self.url, payload, format="json")

        assert response.status_code == status.HTTP_202_ACCEPTED
        row = RecommendationEvent.objects.get()
        assert row.impression_id == response.data["impression_id"]

    def test_two_showings_get_two_ids_each_with_its_own_cart(self, client):
        product = _product()
        carts = [uuid.uuid4(), uuid.uuid4()]
        named = []
        for cart in carts:
            payload = self._payload(product, kind=EventKind.IMPRESSION)
            del payload["impressionId"]
            named.append(
                client.post(
                    self.url, payload, format="json", HTTP_X_CART_ID=str(cart)
                ).data["impression_id"]
            )

        assert named[0] != named[1]
        assert {
            (row.impression_id, row.cart_uuid)
            for row in RecommendationEvent.objects.all()
        } == set(zip(named, carts, strict=True))

    def test_an_impression_that_brings_its_own_id_is_refused(self, client):
        """The showing's id is Django's to mint: a client-made one would let
        a cached or forged id stand for many showings."""
        payload = self._payload(_product(), kind=EventKind.IMPRESSION)

        response = client.post(self.url, payload, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "impression_id_minted"
        assert not RecommendationEvent.objects.exists()

    def test_one_click_reported_twice_is_one_event(self, client):
        """A beacon sent again (a retried request) is the same event: the
        showing's id names it."""
        payload = self._payload(_product(), kind=EventKind.CLICK)

        client.post(self.url, payload, format="json")
        client.post(self.url, payload, format="json")

        assert RecommendationEvent.objects.count() == 1

    def test_a_click_names_the_showing_it_came_from(self, client):
        payload = self._payload(_product(), kind=EventKind.CLICK)
        del payload["impressionId"]

        response = client.post(self.url, payload, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "impression_id_required"
        assert not RecommendationEvent.objects.exists()

    def test_a_broker_failure_drops_the_event_and_still_accepts(
        self, client, caplog
    ):
        """An open analytics beacon never answers 500 for an outage, and
        never turns anonymous traffic into `DeferredDispatch` rows."""
        with patch.object(
            record_recommendation_event,
            "apply_async",
            side_effect=OperationalError("broker unreachable"),
        ):
            response = client.post(
                self.url, self._payload(_product()), format="json"
            )

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert not DeferredDispatch.objects.exists()
        assert not RecommendationEvent.objects.exists()
        assert any(
            f"Dropped {record_recommendation_event.name}" in r.getMessage()
            for r in caplog.records
        )

    def test_in_an_outage_the_next_event_does_not_wait_for_the_broker(
        self, client
    ):
        with patch.object(
            record_recommendation_event,
            "apply_async",
            side_effect=OperationalError("broker unreachable"),
        ) as publish:
            for _ in range(2):
                response = client.post(
                    self.url, self._payload(_product()), format="json"
                )
                assert response.status_code == status.HTTP_202_ACCEPTED

        publish.assert_called_once()

    def test_a_bug_in_the_publish_is_not_swallowed(self, client):
        with (
            patch.object(
                record_recommendation_event,
                "apply_async",
                side_effect=TypeError("bad arguments"),
            ),
            pytest.raises(TypeError),
        ):
            client.post(self.url, self._payload(_product()), format="json")

    def test_the_cart_header_becomes_the_event_identity(self, client):
        product = _product()
        cart = uuid.uuid4()

        response = client.post(
            self.url,
            self._payload(product),
            format="json",
            HTTP_X_CART_ID=str(cart),
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert RecommendationEvent.objects.get().cart_uuid == cart

    def test_a_forged_cart_header_reads_as_no_cart(self, client):
        product = _product()

        response = client.post(
            self.url,
            self._payload(product),
            format="json",
            HTTP_X_CART_ID="42",
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert RecommendationEvent.objects.get().cart_uuid is None

    def test_attach_cannot_be_posted(self, client):
        product = _product()
        response = client.post(
            self.url,
            self._payload(product, kind=EventKind.ATTACH),
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not RecommendationEvent.objects.exists()

    def test_items_are_required_and_bounded(self, client):
        product = _product()
        assert (
            client.post(
                self.url, self._payload(product, items=[]), format="json"
            ).status_code
            == status.HTTP_400_BAD_REQUEST
        )
        too_many = [
            {"productId": product.id, "strategy": StrategyCode.CURATED}
        ] * 25
        assert (
            client.post(
                self.url, self._payload(product, items=too_many), format="json"
            ).status_code
            == status.HTTP_400_BAD_REQUEST
        )
