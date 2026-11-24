"""What the order code asks a carrier, instead of naming it.

A carrier names the relation its shipment row hangs on, what the order
detail reads below it, its shipment's state and last report, its admin
inline, search fields and filters, its own field on the order detail, the
checkout fields it takes and its tracking page. The order manager, the
admin, the follow-up rule, the serializers and the views read those
answers from the registry, so a carrier no order module names shows up
everywhere a built-in one does.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest
from django.contrib.admin import AdminSite
from django.test import RequestFactory
from django.urls import reverse
from rest_framework import serializers, status
from rest_framework.test import APIClient
from unfold.admin import StackedInline

from cart.serializers.cart import CartQuoteRequestSerializer
from order.admin import OrderAdmin
from order.factories.order import OrderFactory
from order.lifecycle.follow_up import last_carrier_event_at
from order.models.attribution import OrderAttribution
from order.models.order import Order
from order.serializers.order import (
    OrderAttributionSerializer,
    OrderCreateFromCartSerializer,
    OrderDetailSerializer,
)
from order.serializers.quote import PickupPointSerializer
from order.views.order import OrderViewSet
from shipping.exceptions import CarrierAPIError
from shipping.interfaces import (
    _REGISTRY,
    OrderShipmentField,
    ShippingCarrierInterface,
    get_provider,
    order_detail_prefetches,
    register_provider,
    shipment_relations,
)
from shipping.models import ShippingProvider
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

FAKE_CODE = "fake_order"
REPORTED_AT = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)


class _FakeRefused(CarrierAPIError):
    @property
    def text(self) -> str:
        return "Too late to cancel"


class _FakeInline(StackedInline):
    model = OrderAttribution


class _FakeCarrier(ShippingCarrierInterface):
    """A carrier whose "shipment" is the order's attribution row: a
    reverse one-to-one every order may have, so no model is needed."""

    code: ClassVar[str] = FAKE_CODE
    shipment_relation: ClassVar[str | None] = "attribution"
    order_detail_prefetches: ClassVar[tuple[str, ...]] = ("attribution",)
    admin_order_search_fields: ClassVar[tuple[str, ...]] = (
        "attribution__campaign",
    )
    admin_order_list_filters: ClassVar[tuple[str, ...]] = (
        "attribution__medium",
    )
    location_keys: ClassVar[tuple[str, ...]] = ("fake_point",)
    field_rank: ClassVar[int] = 2
    tracking_url_template: ClassVar[str | None] = (
        "https://fake.example/track/{number}"
    )

    def create_shipment(self, order, *, kind, payload=None):
        return None

    def cancel_shipment(self, shipment, *, reason="") -> None:
        raise _FakeRefused

    def has_label(self, shipment) -> bool:
        return False

    def fetch_label_bytes(self, shipment) -> bytes:
        return b""

    def fetch_tracking_events(self, shipment) -> list[dict[str, Any]]:
        return []

    def holds_the_parcel(self, shipment) -> bool:
        return False

    def serialize_shipment(self, shipment, *, context):
        return dict(OrderAttributionSerializer(shipment).data)

    def validate_order_payload(self, *, kind, payload):
        return {}

    def shipment_state(self, shipment):
        return "fake_exploded", "Exploded"

    def last_reported_at(self, shipment):
        return REPORTED_AT

    def admin_order_inline(self):
        return _FakeInline

    def checkout_fields(self) -> dict[str, serializers.Field]:
        return {
            "fake_point": serializers.CharField(required=False),
            "fake_fragile": serializers.BooleanField(required=False),
        }

    def order_shipment_field(self) -> OrderShipmentField:
        return OrderShipmentField(
            name="fake_shipment",
            serializer=OrderAttributionSerializer,
            help_text="Fake shipment details, else null.",
        )


@pytest.fixture
def fake_carrier():
    register_provider(_FakeCarrier)
    yield get_provider(FAKE_CODE)
    _REGISTRY.pop(FAKE_CODE, None)


def _fake_order(**fields) -> Order:
    provider = ShippingProvider.objects.create(
        code=FAKE_CODE, name="Fake", is_active=True
    )
    order = OrderFactory(num_order_items=0, **fields)
    order.shipping_provider = provider
    order.save(update_fields=["shipping_provider"])
    OrderAttribution.objects.create(order=order, campaign="autumn")
    return Order.objects.with_shipments().get(pk=order.pk)


def _request(user=None):
    request = RequestFactory().get("/admin/order/order/")
    request.user = user or UserAccountFactory(is_staff=True, is_superuser=True)
    return request


class TestInterfaceDefaults:
    def test_the_flat_rate_carrier_has_no_shipment_on_the_order(self):
        flat = get_provider("flat_rate")

        assert flat.shipment_relation is None
        assert flat.order_detail_prefetches == ()
        assert flat.shipment_for_order(OrderFactory.build()) is None
        assert flat.admin_order_inline() is None
        assert flat.order_shipment_field() is None
        assert flat.admin_order_search_fields == ()
        assert flat.admin_order_list_filters == ()
        assert flat.checkout_fields() == {}
        assert flat.tracking_url_template is None

    def test_the_built_in_carriers_name_their_relations(self):
        acs = get_provider("acs")
        boxnow = get_provider("boxnow")

        assert (acs.shipment_relation, boxnow.shipment_relation) == (
            "acs_shipment",
            "boxnow_shipment",
        )
        assert acs.order_detail_prefetches == (
            "acs_shipment",
            "acs_shipment__events",
            "acs_shipment__station_destination",
        )
        assert boxnow.order_detail_prefetches == ("boxnow_shipment",)
        assert [
            carrier.order_shipment_field().name for carrier in (boxnow, acs)
        ] == ["boxnow_shipment", "acs_shipment"]

    def test_a_carriers_shipment_is_its_relation_on_the_order(
        self, fake_carrier
    ):
        order = _fake_order()

        assert fake_carrier.shipment_for_order(order) == order.attribution


class TestQueries:
    def test_the_shipments_are_joined_for_every_carrier(self, fake_carrier):
        joined = Order.objects.with_shipments().query.select_related

        assert {"acs_shipment", "boxnow_shipment", "attribution"} <= set(joined)
        assert "attribution" in shipment_relations()

    def test_the_detail_prefetches_what_every_carrier_reads(self, fake_carrier):
        lookups = Order.objects.for_detail()._prefetch_related_lookups

        assert set(order_detail_prefetches()) <= set(lookups)
        assert "acs_shipment__events" in lookups
        assert "attribution" in order_detail_prefetches()


class TestFollowUp:
    def test_the_last_report_is_asked_of_every_carrier(self, fake_carrier):
        assert last_carrier_event_at(_fake_order()) == REPORTED_AT


def _filter_names(admin: OrderAdmin, request) -> list[Any]:
    """The field each list filter filters on (Unfold's ``(field, Filter)``
    pairs, as ``BaseModelAdmin`` turns every plain name into one)."""
    return [
        entry[0] if isinstance(entry, tuple) else entry
        for entry in admin.get_list_filter(request)
    ]


class TestAdmin:
    def _admin(self) -> OrderAdmin:
        return OrderAdmin(Order, AdminSite())

    def test_the_carriers_inline_is_shown_for_its_orders(self, fake_carrier):
        order = _fake_order()

        assert _FakeInline in self._admin().get_inlines(_request(), order)

    def test_no_carrier_inline_for_another_carriers_order(self, fake_carrier):
        order = OrderFactory(num_order_items=0)

        assert _FakeInline not in self._admin().get_inlines(_request(), order)

    def test_the_shipment_state_is_the_carriers(self, fake_carrier):
        assert self._admin().shipment_state(_fake_order()) == (
            "fake_exploded",
            "Exploded",
        )

    def test_a_carrier_adds_its_search_fields_and_filters(self, fake_carrier):
        admin = self._admin()
        request = _request()

        assert "attribution__campaign" in admin.get_search_fields(request)
        assert "attribution__medium" in _filter_names(admin, request)

    def test_the_built_in_carriers_keep_their_search_and_filter(self):
        admin = self._admin()
        request = _request()

        assert list(admin.get_search_fields(request))[-3:] == [
            "boxnow_shipment__parcel_id",
            "boxnow_shipment__delivery_request_id",
            "boxnow_shipment__locker_external_id",
        ]
        assert _filter_names(admin, request)[-1] == (
            "boxnow_shipment__parcel_state"
        )


class TestDetailSerializer:
    def _data(self, order: Order) -> dict[str, Any]:
        return OrderDetailSerializer(order).data

    def test_a_carriers_field_sits_before_the_generic_shipment(
        self, fake_carrier
    ):
        names = list(OrderDetailSerializer().fields)
        at = names.index("shipment")

        assert names[at - 3 : at] == [
            "boxnow_shipment",
            "acs_shipment",
            "fake_shipment",
        ]

    def test_it_holds_the_shipment_of_its_orders(self, fake_carrier):
        data = self._data(_fake_order())

        assert data["fake_shipment"]["campaign"] == "autumn"
        assert data["fake_shipment"] == data["shipment"]
        assert data["acs_shipment"] is None
        assert data["boxnow_shipment"] is None

    def test_it_is_null_for_another_carriers_order(self, fake_carrier):
        order = OrderFactory(num_order_items=0)
        OrderAttribution.objects.create(order=order, campaign="autumn")

        assert self._data(order)["fake_shipment"] is None


class TestCancelView:
    def test_a_carriers_refusal_is_answered_with_its_text(self, fake_carrier):
        admin = UserAccountFactory(is_staff=True, is_superuser=True)
        order = _fake_order(user=admin)

        client = APIClient()
        client.force_authenticate(user=admin)
        response = client.post(
            reverse("order-shipment-cancel", kwargs={"pk": order.id}),
            {"reason": "changed my mind"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": "Too late to cancel",
            "code": "carrier_cancellation_rejected",
        }


class TestCheckoutFields:
    def test_the_pickup_point_fields_lead_every_body_that_names_one(
        self, fake_carrier
    ):
        for serializer in (
            PickupPointSerializer(),
            CartQuoteRequestSerializer(),
            OrderCreateFromCartSerializer(),
        ):
            assert list(serializer.fields)[:4] == [
                "boxnow_locker_id",
                "acs_station_external_id",
                "acs_station_branch",
                "fake_point",
            ]

    def test_a_carriers_order_options_follow_the_shipping_kind(
        self, fake_carrier
    ):
        names = list(OrderCreateFromCartSerializer().fields)
        at = names.index("shipping_kind")

        assert names[at + 1 : at + 4] == [
            "acs_charge_type",
            "acs_item_quantity",
            "fake_fragile",
        ]
        assert "fake_fragile" not in PickupPointSerializer().fields
        assert "fake_fragile" not in CartQuoteRequestSerializer().fields

    def test_order_creation_hands_the_carrier_what_the_body_sent(
        self, fake_carrier
    ):
        payload = OrderViewSet()._build_shipping_address_from_validated(
            {"fake_point": "P-1", "fake_fragile": True, "acs_item_quantity": 2}
        )

        assert payload["fake_point"] == "P-1"
        assert payload["fake_fragile"] is True
        assert payload["acs_item_quantity"] == 2
        assert "boxnow_locker_id" not in payload


class TestTrackingLink:
    def _url(self, order: Order) -> str | None:
        return OrderDetailSerializer(order).data["tracking_details"][
            "tracking_url"
        ]

    def test_a_carriers_link_is_its_own_template(self, fake_carrier):
        order = _fake_order(tracking_number="F123", shipping_carrier=FAKE_CODE)

        assert self._url(order) == "https://fake.example/track/F123"

    def test_the_built_in_carriers_and_other_couriers_keep_theirs(self):
        acs = OrderFactory(
            num_order_items=0, tracking_number="7001", shipping_carrier="ACS"
        )
        elta = OrderFactory(
            num_order_items=0, tracking_number="RR1", shipping_carrier="elta"
        )

        assert self._url(acs) == (
            "https://webapp.acscourier.net/track-shipment/7001"
        )
        assert self._url(elta) == "https://www.elta.gr/en/tracking?code=RR1"
