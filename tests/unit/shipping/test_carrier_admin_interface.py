"""What the admin asks a carrier, instead of branching on which one it is.

A carrier describes its admin pages, its shipment-state colours and the
work it leaves waiting; the sidebar, the displays and the dashboard read
those answers from the registry. A carrier no admin module names must show
up everywhere a built-in one does, and one the store does not use must
leave the sidebar.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

import pytest
from django.test import RequestFactory
from django.utils import timezone, translation

from admin import displays
from admin.dashboard.store import queries
from admin.features import StoreFeatures
from admin.navigation import store_sidebar
from shipping.interfaces import (
    _REGISTRY,
    AdminPage,
    AdminPages,
    AttentionRow,
    ShippingCarrierInterface,
    get_provider,
    register_provider,
    registered_providers,
)
from shipping.models import ShippingProvider
from tests.utils.staff import (
    bind_store_tenant,
    stamp_platform_identity,
    store_tenant,
    unbind_store_tenant,
)
from tests.utils.store_settings import switch_every_feature_on
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

FAKE_CODE = "fake_admin"


class _FakeCarrier(ShippingCarrierInterface):
    code: ClassVar[str] = FAKE_CODE
    app_label: ClassVar[str | None] = "fake_admin_app"
    attention_models: ClassVar[tuple[str, ...]] = ("shipping.ShippingRate",)

    def create_shipment(self, order, *, kind, payload=None):
        return None

    def cancel_shipment(self, shipment, *, reason="") -> None:
        return None

    def has_label(self, shipment) -> bool:
        return False

    def fetch_label_bytes(self, shipment) -> bytes:
        return b""

    def fetch_tracking_events(self, shipment) -> list[dict[str, Any]]:
        return []

    def shipment_for_order(self, order):
        return None

    def holds_the_parcel(self, shipment) -> bool:
        return False

    def serialize_shipment(self, shipment, *, context):
        return None

    def validate_order_payload(self, *, kind, payload):
        return {}

    def admin_pages(self) -> AdminPages:
        return AdminPages(
            shipments=(
                AdminPage("shipping.ShippingProvider", "Fake Shipments", "box"),
            )
        )

    def shipment_state_variants(self) -> dict[str, str]:
        return {"fake_exploded": "danger"}

    def attention_counts(self, now: datetime) -> dict[str, int]:
        return {"fake_waiting": 3}

    def attention_rows(self, counts) -> tuple[AttentionRow, ...]:
        return (
            AttentionRow(
                count_key="fake_waiting",
                icon="bolt",
                tone="warning",
                title="Fake work",
                description="Waiting",
                model="shipping.ShippingRate",
                query="x=1",
            ),
        )


@pytest.fixture
def fake_carrier():
    register_provider(_FakeCarrier)
    yield get_provider(FAKE_CODE)
    _REGISTRY.pop(FAKE_CODE, None)


@pytest.fixture
def english():
    with translation.override("en"):
        yield


def _titles(navigation) -> set[str]:
    found: set[str] = set()

    def walk(items):
        for item in items:
            found.add(str(item["title"]))
            walk(item.get("items", []))

    for group in navigation:
        walk(group["items"])
    return found


def _sidebar() -> list[dict[str, Any]]:
    switch_every_feature_on()
    tenant = store_tenant("carrier_admin_store")
    previous = bind_store_tenant(tenant)
    try:
        request = RequestFactory().get("/admin/")
        request.user = stamp_platform_identity(UserAccountFactory(admin=True))
        return store_sidebar(request)
    finally:
        unbind_store_tenant(previous)


class TestInterfaceDefaults:
    def test_the_flat_rate_carrier_adds_nothing_to_the_admin(self):
        flat = get_provider("flat_rate")

        assert flat.app_label is None
        assert flat.admin_pages() == AdminPages()
        assert flat.shipment_state_variants() == {}
        assert flat.attention_counts(timezone.now()) == {}
        assert flat.attention_rows({}) == ()
        assert flat.attention_models == ()

    def test_the_built_in_carriers_name_their_app_and_pages(self):
        acs = get_provider("acs")
        boxnow = get_provider("boxnow")

        assert (acs.app_label, boxnow.app_label) == (
            "shipping_acs",
            "shipping_boxnow",
        )
        assert {p.model for p in acs.admin_pages().shipments} == {
            "shipping_acs.AcsShipment",
            "shipping_acs.AcsUnrecognisedVoucher",
            "shipping_acs.AcsVoucherHold",
        }
        assert [p.model for p in boxnow.admin_pages().pickup_points] == [
            "shipping_boxnow.BoxNowLocker"
        ]
        assert acs.shipment_state_variants()["delivered"] == "success"
        assert boxnow.shipment_state_variants()["missing"] == "danger"
        assert "shipping_acs.AcsVoucherHold" in acs.attention_models


class TestStateDisplay:
    def test_a_carrier_state_colours_the_shared_display(self, fake_carrier):
        assert displays.SHIPMENT_STATE_VARIANT["fake_exploded"] == "danger"
        assert displays.SHIPMENT_STATE_VARIANT.get("delivered") == "success"
        assert "fake_exploded" in displays.SHIPMENT_STATE_VARIANT

    def test_it_is_a_dict_unfold_accepts(self):
        assert isinstance(displays.SHIPMENT_STATE_VARIANT, dict)

    def test_without_the_carrier_the_state_is_unknown(self):
        assert "fake_exploded" not in displays.SHIPMENT_STATE_VARIANT


class TestInactiveCarriers:
    def test_a_carrier_without_an_active_provider_row_is_inactive(
        self, fake_carrier
    ):
        inactive = StoreFeatures.read().inactive_carrier_apps()

        assert "fake_admin_app" in inactive

        ShippingProvider.objects.create(
            code=FAKE_CODE, name="Fake", is_active=True
        )

        assert (
            "fake_admin_app" not in StoreFeatures.read().inactive_carrier_apps()
        )

    def test_the_flat_rate_is_never_a_carrier_app(self):
        assert None not in StoreFeatures.read().inactive_carrier_apps()

    def test_a_store_uses_a_carrier_app_while_one_is_active(self, fake_carrier):
        ShippingProvider.objects.filter(
            code__in=[c.code for c in registered_providers()]
        ).update(is_active=False)

        assert StoreFeatures.read().uses_a_carrier_app() is False

        ShippingProvider.objects.create(
            code=FAKE_CODE, name="Fake", is_active=True
        )

        assert StoreFeatures.read().uses_a_carrier_app() is True


class TestSidebar:
    def test_a_carriers_pages_list_while_it_is_active(
        self, fake_carrier, english
    ):
        ShippingProvider.objects.create(
            code=FAKE_CODE, name="Fake", is_active=True
        )

        assert "Fake Shipments" in _titles(_sidebar())

    def test_an_inactive_carriers_pages_do_not(self, fake_carrier, english):
        assert "Fake Shipments" not in _titles(_sidebar())


class TestAttention:
    def test_every_carriers_counts_join_the_dashboard(self, fake_carrier):
        counts = queries.attention_counts.compute()

        assert counts["fake_waiting"] == 3
        assert counts["acs_voucher_holds"] == 0

    def test_a_carriers_models_clear_the_cached_counts(self, fake_carrier):
        from admin.dashboard.cache import keys_depending_on

        assert keys_depending_on("shipping.ShippingRate") == [
            "admin:dashboard:store:attention:v4"
        ]
        assert keys_depending_on("shipping_acs.AcsVoucherHold") == [
            "admin:dashboard:store:attention:v4"
        ]


class TestCachedCountsAfterTheRealStartup:
    """The admin's ``ready()`` runs before the carrier apps register, so
    the built-in carriers' models are wired to the cached counts only by
    the ``carrier_registered`` receiver. No fake carrier here: the
    registry is what startup filled."""

    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        from django.core.cache import cache

        cache.clear()

    def _cached(self) -> bool:
        from django.core.cache import cache

        return cache.get(queries.attention_counts.key()) is not None

    def test_an_acs_write_clears_the_cached_counts(
        self, django_capture_on_commit_callbacks
    ):
        from shipping_acs.models import AcsVoucherHold

        queries.attention_counts.get()
        assert self._cached()

        with django_capture_on_commit_callbacks(execute=True):
            now = timezone.now()
            AcsVoucherHold.objects.create(
                started_at=now, last_failure_at=now, reason="ACS 502"
            )

        assert not self._cached()

    def test_a_boxnow_write_clears_the_cached_counts(
        self, django_capture_on_commit_callbacks
    ):
        from shipping_boxnow.factories import BoxNowShipmentFactory

        # The factory writes an order too; only the shipment's own write
        # is under test, so it is made before the counts are cached.
        shipment = BoxNowShipmentFactory()
        queries.attention_counts.get()
        assert self._cached()

        with django_capture_on_commit_callbacks(execute=True):
            shipment.save()

        assert not self._cached()
