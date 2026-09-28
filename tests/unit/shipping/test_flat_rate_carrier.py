"""The platform's own carrier — the one a store gets without a contract.

Every other adapter gates on tenant credentials, which is right for them
and left a hole: a store with no courier contract had no shipping option
at all, so ``/shipping/options`` returned ``[]`` and checkout dead-ended
at the delivery step. These tests pin the two things that make this
adapter close that hole rather than merely exist — it is always
registered and it needs no credentials.

Pricing is a ``ShippingRate`` row now, not this adapter's job — see
``tests/unit/shipping/test_shipping_service_rates.py`` for the quote/
availability/weight-cap tests this file used to carry.
"""

from __future__ import annotations

import pytest
from django.test import TestCase

from shipping.carriers.flat_rate import FlatRateCarrier
from shipping.enum import ShippingKind
from shipping.interfaces import get_provider, is_registered


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

    def test_it_has_no_live_quote_of_its_own(self):
        """The default ``live_quote`` — ``ShippingRate.price`` unchanged.

        A merchant quoting a single flat number to their customers is
        the whole point of this carrier; it has nothing to override.
        """
        carrier = FlatRateCarrier()
        assert (
            carrier.live_quote(
                rate=None,
                country_code="GR",
                weight_grams=None,
                currency="EUR",
            )
            is None
        )


class TestFulfilment(TestCase):
    """A merchant-fulfilled parcel has no remote anything."""

    def setUp(self):
        self.carrier = FlatRateCarrier()

    def test_it_can_take_cash_at_the_door(self):
        """The capability a locker does not have — and the reason COD
        must survive here."""
        from pay_way.enum.settlement import PaySettlement

        supported = self.carrier.supported_settlements(
            ShippingKind.HOME_DELIVERY
        )
        assert PaySettlement.COURIER_CASH in supported
        assert PaySettlement.ONLINE in supported

    def test_it_never_offers_a_carrier_collected_payment(self):
        """`CARRIER_TERMINAL` is the CARRIER collecting after dispatch
        by sending its own payment link. There is no carrier here.

        Declaring no constraint at all put BOX NOW PAY ON THE GO against
        flat-rate home delivery in checkout — a carrier collecting for a
        parcel it never touches.
        """
        from pay_way.enum.settlement import PaySettlement

        supported = self.carrier.supported_settlements(
            ShippingKind.HOME_DELIVERY
        )
        assert PaySettlement.CARRIER_TERMINAL not in supported

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
