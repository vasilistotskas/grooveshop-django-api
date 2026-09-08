"""Layer 2 of the pay-way filter: what BoxNow can physically settle.

This is regression coverage for a defect that shipped and charged real
customers, so the cases are written against the *money*, not the code
shape.

History. ``891d5663`` / ``8fb8dcbc`` (2026-05-01) enabled BoxNow PAY ON
THE GO by deleting a filter that had excluded **all offline** pay-ways.
Correct in intent — the product was live on the partner account — but it
left the generic courier ``cash_on_delivery`` pay-way selectable on
lockers, and ``PayWay.is_cash_on_delivery`` was true for both products.
The result: a shopper picking "Αντικαταβολή (+1,99 €)" with a locker got
a BoxNow COD voucher whose ``amountToBeCollected`` included the €1,99
cash-handling surcharge, then paid by card at a terminal that takes no
cash and involves no courier.

The fix is a capability declaration, not another boolean. If these
tests are ever "fixed" by widening BoxNow to accept ``COURIER_CASH``,
that defect is back.
"""

from __future__ import annotations

from django.test import TestCase

from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from pay_way.models import PayWay
from pay_way.services import PayWayService
from shipping.enum import ShippingKind
from shipping.factories import ShippingProviderFactory
from shipping_boxnow.carrier import BoxNowCarrier


class BoxNowSupportedSettlementsTests(TestCase):
    def setUp(self):
        self.carrier = BoxNowCarrier()

    def test_lockers_reject_courier_cash(self):
        supported = self.carrier.supported_settlements(
            ShippingKind.PICKUP_POINT
        )
        assert supported is not None
        self.assertNotIn(PaySettlement.COURIER_CASH, supported)

    def test_lockers_accept_the_terminal_product(self):
        supported = self.carrier.supported_settlements(
            ShippingKind.PICKUP_POINT
        )
        assert supported is not None
        # PAY ON THE GO. Removing this re-breaks what 891d5663 fixed.
        self.assertIn(PaySettlement.CARRIER_TERMINAL, supported)
        self.assertIn(PaySettlement.ONLINE, supported)
        self.assertIn(PaySettlement.OFFLINE_TRANSFER, supported)

    def test_home_delivery_is_unconstrained(self):
        # BoxNow's constraint is a property of the locker, not of the
        # carrier, so a non-locker kind must not inherit it.
        self.assertIsNone(
            self.carrier.supported_settlements(ShippingKind.HOME_DELIVERY)
        )


class BoxNowPayWayFilterTests(TestCase):
    """The declaration reaching the storefront through the real service."""

    def setUp(self):
        ShippingProviderFactory(code="boxnow")
        self.courier_cash = PayWayFactory(
            active=True,
            provider_code="cash_on_delivery",
            settlement=PaySettlement.COURIER_CASH,
        )
        self.pay_on_the_go = PayWayFactory.create_carrier_terminal_payment()
        self.online = PayWayFactory.create_online_payment()

    def _offered(self, kind: ShippingKind) -> set[int]:
        qs = PayWayService.filter_by_carrier(
            PayWay.objects.all(),
            provider_code="boxnow",
            shipping_kind=kind.value,
        )
        return set(qs.values_list("id", flat=True))

    def test_courier_cod_is_not_offered_on_a_locker(self):
        # The exact combination that mis-charged customers.
        offered = self._offered(ShippingKind.PICKUP_POINT)
        self.assertNotIn(self.courier_cash.id, offered)

    def test_pay_on_the_go_is_offered_on_a_locker(self):
        offered = self._offered(ShippingKind.PICKUP_POINT)
        self.assertIn(self.pay_on_the_go.id, offered)

    def test_online_is_still_offered_on_a_locker(self):
        offered = self._offered(ShippingKind.PICKUP_POINT)
        self.assertIn(self.online.id, offered)

    def test_no_exclusion_row_is_required_for_any_of_this(self):
        # The old plan was a PayWayShippingExclusion row per partner.
        # It is a hard product constraint, not an operator preference,
        # so it must hold with an empty exclusion table — a merchant
        # cannot opt into collecting cash at a machine that has no cash
        # drawer.
        from pay_way.models import PayWayShippingExclusion

        self.assertFalse(PayWayShippingExclusion.objects.exists())
        self.assertNotIn(
            self.courier_cash.id, self._offered(ShippingKind.PICKUP_POINT)
        )
