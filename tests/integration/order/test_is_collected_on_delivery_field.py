"""``OrderSerializer.is_collected_on_delivery`` — the field the
storefront needs to stop showing a COD shopper a payment warning.

Why a dedicated field rather than reusing ``is_online_payment``: that
flag is false for bank transfer too, where the shopper pays us directly
and owes the courier nothing. Telling such a customer "you will pay on
delivery" is wrong, so the success page cannot key its green panel on
the negation of an online-payment flag.

The bug this closes: a cash-on-delivery order sat on the success page
under an amber "Η πληρωμή επεξεργάζεται / payment confirmation may be
delayed a few minutes" alert. COD payment status stays PENDING by
design until the carrier remits — measured ACS lag is about four days —
so that "few minutes" warning was on screen for days for an order the
shopper had not been asked to pay for yet.
"""

from __future__ import annotations

from django.test import TestCase

from order.factories import OrderFactory
from order.serializers.order import OrderSerializer
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory


class IsCollectedOnDeliveryFieldTests(TestCase):
    def _field(self, pay_way) -> bool:
        order = OrderFactory(pay_way=pay_way)
        return OrderSerializer(order).data["is_collected_on_delivery"]

    def test_true_for_courier_cash_on_delivery(self):
        pay_way = PayWayFactory(settlement=PaySettlement.COURIER_CASH)
        self.assertTrue(self._field(pay_way))

    def test_true_for_a_carrier_terminal_payment(self):
        # BoxNow PAY ON THE GO. Collected on delivery just like courier
        # COD, even though the machine takes a card rather than cash —
        # the shopper still owes money at handover, which is what the
        # storefront panel is about.
        pay_way = PayWayFactory(settlement=PaySettlement.CARRIER_TERMINAL)
        self.assertTrue(self._field(pay_way))

    def test_false_for_an_online_payment(self):
        pay_way = PayWayFactory(settlement=PaySettlement.ONLINE)
        self.assertFalse(self._field(pay_way))

    def test_false_for_bank_transfer(self):
        # The case that makes this field necessary. ``is_online_payment``
        # is ALSO false here, so a storefront keying on that would have
        # promised "pay on delivery" to someone who must transfer the
        # money to us themselves.
        pay_way = PayWayFactory(settlement=PaySettlement.OFFLINE_TRANSFER)
        self.assertFalse(self._field(pay_way))

    def test_it_disagrees_with_is_online_payment_for_bank_transfer(self):
        pay_way = PayWayFactory(settlement=PaySettlement.OFFLINE_TRANSFER)
        order = OrderFactory(pay_way=pay_way)

        data = OrderSerializer(order).data

        # Both false — the two fields are genuinely answering different
        # questions, and this is the pair that proves it.
        self.assertFalse(data["is_online_payment"])
        self.assertFalse(data["is_collected_on_delivery"])

    def test_false_when_the_order_has_no_pay_way(self):
        # Guest/abandoned orders can carry a null pay-way; the field
        # must not raise.
        self.assertFalse(self._field(None))

    def test_present_on_the_serialized_payload(self):
        pay_way = PayWayFactory(settlement=PaySettlement.COURIER_CASH)
        order = OrderFactory(pay_way=pay_way)

        # Guards the field tuple, not just the method: it has to be in
        # BOTH the list and detail field sets or the success page reads
        # undefined and silently falls through to the amber branch.
        self.assertIn("is_collected_on_delivery", OrderSerializer(order).data)
