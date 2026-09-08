"""Layer 2 of the pay-way filter: what ACS can physically settle.

The mirror of the BoxNow suite. ACS carries cash and a POS, so
``COURIER_CASH`` is its native collect-on-delivery product — and
``CARRIER_TERMINAL`` (BoxNow PAY ON THE GO, paid at a locker machine)
must never ride an ACS voucher, or a courier would be asked to collect
money the shopper pays at another company's hardware.

Before ``PaySettlement`` existed both products were indistinguishable
through ``PayWay.is_cash_on_delivery``, so nothing stopped them
crossing carriers.
"""

from __future__ import annotations

from django.test import TestCase

from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from pay_way.models import PayWay
from pay_way.services import PayWayService
from shipping.enum import ShippingKind
from shipping.factories import ShippingProviderFactory
from shipping_acs.carrier import AcsCarrier


class AcsSupportedSettlementsTests(TestCase):
    def setUp(self):
        self.carrier = AcsCarrier()

    def test_rejects_the_locker_terminal_product_on_every_kind(self):
        for kind in ShippingKind:
            with self.subTest(kind=kind.value):
                supported = self.carrier.supported_settlements(kind)
                assert supported is not None
                self.assertNotIn(PaySettlement.CARRIER_TERMINAL, supported)

    def test_accepts_courier_cash_on_every_kind(self):
        for kind in ShippingKind:
            with self.subTest(kind=kind.value):
                supported = self.carrier.supported_settlements(kind)
                assert supported is not None
                self.assertIn(PaySettlement.COURIER_CASH, supported)
                self.assertIn(PaySettlement.ONLINE, supported)
                self.assertIn(PaySettlement.OFFLINE_TRANSFER, supported)


class AcsPayWayFilterTests(TestCase):
    def setUp(self):
        ShippingProviderFactory(code="acs")
        self.courier_cash = PayWayFactory(
            active=True,
            provider_code="cash_on_delivery",
            settlement=PaySettlement.COURIER_CASH,
        )
        self.pay_on_the_go = PayWayFactory.create_carrier_terminal_payment()

    def _offered(self, kind: ShippingKind) -> set[int]:
        qs = PayWayService.filter_by_carrier(
            PayWay.objects.all(),
            provider_code="acs",
            shipping_kind=kind.value,
        )
        return set(qs.values_list("id", flat=True))

    def test_courier_cod_is_offered(self):
        # ACS's own product must keep working — this refactor must not
        # quietly take cash-on-delivery away from home delivery.
        self.assertIn(
            self.courier_cash.id, self._offered(ShippingKind.HOME_DELIVERY)
        )

    def test_boxnow_pay_on_the_go_is_never_offered_on_acs(self):
        for kind in ShippingKind:
            with self.subTest(kind=kind.value):
                self.assertNotIn(self.pay_on_the_go.id, self._offered(kind))
