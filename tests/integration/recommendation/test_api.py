"""The two public endpoints on the wire: shape, gating, and the
impression/click rows they leave behind."""

from __future__ import annotations

import uuid

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from product.enum.relation import RelationType
from product.factories.product import ProductFactory
from product.models import ProductRelation
from recommendation.enum import EventKind, StrategyCode, Surface
from recommendation.models import RecommendationEvent
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)

pytestmark = pytest.mark.django_db


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

        uuid.UUID(str(data["impression_id"]))
        # The read path writes nothing: the impression is the client's
        # to report once the strip is actually shown, so a cached or
        # never-scrolled-to response cannot inflate a strategy's
        # impression count.
        assert not RecommendationEvent.objects.exists()

    def test_wire_shape_is_camel_case(self, client, curated_seed):
        seed, _ = curated_seed
        body = client.get(self.url, {"seed": seed.id}).json()
        assert "impressionId" in body
        assert "relationType" in body["items"][0]["reason"]

    def test_nothing_worth_showing_is_an_empty_list(self, client):
        seed = _product()

        response = client.get(self.url, {"seed": seed.id})

        assert response.status_code == status.HTTP_200_OK
        assert response.data["items"] == []
        assert not RecommendationEvent.objects.exists()

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
