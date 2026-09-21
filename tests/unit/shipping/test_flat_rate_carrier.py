"""The platform's own carrier — the one a store gets without a contract.

Every other adapter gates on tenant credentials, which is right for them
and left a hole: a store with no courier contract had no shipping option
at all, so ``/shipping/options`` returned ``[]`` and checkout dead-ended
at the delivery step. These tests pin the three things that make this
adapter close that hole rather than merely exist — it is always
registered, it needs no credentials, and it prices from the settings the
platform already treats as "the store's shipping price".
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.test import TestCase
from extra_settings.models import Setting

from shipping.carriers.flat_rate import (
    DEFAULT_FREE_THRESHOLD,
    DEFAULT_PRICE,
    FlatRateCarrier,
)
from shipping.enum import ShippingKind
from shipping.interfaces import get_provider, is_registered


def _money_setting(name: str, value: str) -> None:
    Setting.objects.update_or_create(
        name=name,
        defaults={"value_type": Setting.TYPE_DECIMAL, "value_decimal": value},
    )


class TestRegistration(TestCase):
    def test_it_is_registered_without_any_carrier_app(self):
        """Registered by ``shipping`` itself, at AppConfig.ready().

        A deployment that omitted a carrier app would otherwise leave
        every uncontracted store unable to take an order.
        """
        assert is_registered("flat_rate")
        assert isinstance(get_provider("flat_rate"), FlatRateCarrier)

    def test_it_asks_for_no_per_order_payload(self):
        """No locker, no pickup point, nothing for the shopper to pick."""
        assert FlatRateCarrier.payload_keys == ()

    def test_every_kind_is_enabled_without_credentials(self):
        """The point of the adapter.

        ACS and BoxNow answer False here when the tenant has no
        credentials. This one has none to have.
        """
        carrier = FlatRateCarrier()
        assert carrier.is_kind_enabled(ShippingKind.HOME_DELIVERY) is True


class TestPricing(TestCase):
    def setUp(self):
        self.carrier = FlatRateCarrier()

    def test_it_charges_the_store_price_below_the_threshold(self):
        _money_setting("CHECKOUT_SHIPPING_PRICE", "4.50")
        _money_setting("FREE_SHIPPING_THRESHOLD", "40.00")

        quote = self.carrier.calculate_shipping_cost(
            order_value_amount=10.0,
            currency="EUR",
            kind=ShippingKind.HOME_DELIVERY,
        )

        assert quote == (4.50, "EUR")

    def test_it_ships_free_at_and_above_the_threshold(self):
        _money_setting("CHECKOUT_SHIPPING_PRICE", "4.50")
        _money_setting("FREE_SHIPPING_THRESHOLD", "40.00")

        for amount in (40.0, 99.0):
            with self.subTest(amount=amount):
                quote = self.carrier.calculate_shipping_cost(
                    order_value_amount=amount,
                    currency="EUR",
                    kind=ShippingKind.HOME_DELIVERY,
                )
                assert quote == (0.0, "EUR")

    def test_it_falls_back_to_the_platform_figures(self):
        """A store that never touched its settings still gets a price.

        The defaults match ``OrderService``'s own fallback, so the two
        paths cannot disagree about what the store charges.
        """
        Setting.objects.filter(
            name__in=["CHECKOUT_SHIPPING_PRICE", "FREE_SHIPPING_THRESHOLD"]
        ).delete()

        quote = self.carrier.calculate_shipping_cost(
            order_value_amount=1.0,
            currency="EUR",
            kind=ShippingKind.HOME_DELIVERY,
        )

        assert quote == (float(DEFAULT_PRICE), "EUR")
        assert (
            self.carrier.free_shipping_threshold(ShippingKind.HOME_DELIVERY)
            == DEFAULT_FREE_THRESHOLD
        )

    def test_a_malformed_setting_does_not_break_checkout(self):
        """Operator-authored rows are free text in the admin.

        A price of "free" must not raise out of the delivery step — the
        store falls back to the platform figure and keeps selling.
        """
        Setting.objects.update_or_create(
            name="CHECKOUT_SHIPPING_PRICE",
            defaults={
                "value_type": Setting.TYPE_STRING,
                "value_string": "free",
            },
        )

        quote = self.carrier.calculate_shipping_cost(
            order_value_amount=1.0,
            currency="EUR",
            kind=ShippingKind.HOME_DELIVERY,
        )

        assert quote == (float(DEFAULT_PRICE), "EUR")

    def test_it_declines_to_price_a_pickup_point(self):
        """It has no locker network — saying nothing is the honest answer.

        A price here would put a phantom pickup option in checkout for a
        carrier that cannot fulfil one.
        """
        assert (
            self.carrier.calculate_shipping_cost(
                order_value_amount=10.0,
                currency="EUR",
                kind=ShippingKind.PICKUP_POINT,
            )
            is None
        )
        assert (
            self.carrier.free_shipping_threshold(ShippingKind.PICKUP_POINT)
            is None
        )

    def test_the_advertised_threshold_matches_the_quote(self):
        """The PDP line and the checkout price must agree.

        They are read from one row for exactly this reason.
        """
        _money_setting("CHECKOUT_SHIPPING_PRICE", "2.00")
        _money_setting("FREE_SHIPPING_THRESHOLD", "25.00")

        threshold = self.carrier.free_shipping_threshold(
            ShippingKind.HOME_DELIVERY
        )
        assert threshold == Decimal("25.00")

        at_threshold = self.carrier.calculate_shipping_cost(
            order_value_amount=float(threshold),
            currency="EUR",
            kind=ShippingKind.HOME_DELIVERY,
        )
        assert at_threshold == (0.0, "EUR")


class TestFulfilment(TestCase):
    """A merchant-fulfilled parcel has no remote anything."""

    def setUp(self):
        self.carrier = FlatRateCarrier()

    def test_it_settles_by_any_means(self):
        """No protocol constraint — a person at a door can take cash.

        The restriction that exists for an unattended locker does not
        exist here, and declaring one would silently remove COD.
        """
        assert (
            self.carrier.supported_settlements(ShippingKind.HOME_DELIVERY)
            is None
        )

    def test_it_has_nothing_to_track_or_cancel(self):
        assert self.carrier.fetch_tracking_events(None) == []
        assert self.carrier.shipment_for_order(None) is None
        assert (
            self.carrier.create_shipment(None, kind=ShippingKind.HOME_DELIVERY)
            is None
        )
        assert self.carrier.cancel_shipment(None) is None

    def test_it_accepts_any_order_payload(self):
        assert (
            self.carrier.validate_order_payload(
                kind=ShippingKind.HOME_DELIVERY, payload={}
            )
            == {}
        )

    def test_asking_for_a_label_fails_loudly(self):
        """Rather than handing the operator a zero-byte PDF."""
        with pytest.raises(NotImplementedError):
            self.carrier.fetch_label_bytes(None)
