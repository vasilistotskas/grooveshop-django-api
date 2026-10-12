"""What the customer still has to pay: ``Order.remaining_amount``.

``Order.amount_due`` is what the order costs the customer, fixed at
creation. ``remaining_amount`` is all of it until the payment settles and
nothing once it has (``SETTLED_PAYMENT_STATUSES``); it was 0 on every
order while the field was read as money already collected
(``paid_amount``).

It is read from ``payment_status`` and never from the payment ledger: a
settled order can hold no ledger row (paid by hand, cash collected under a
``payment_method`` the ledger backfill does not key on, the demo seed), and
would read as unpaid.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.factories.payment_transaction import PaymentTransactionFactory
from order.payment_ledger import PaymentLedger
from order.serializers.order import OrderDetailSerializer
from order.services import OrderService
from pay_way.factories import PayWayFactory

pytestmark = pytest.mark.django_db

DUE = Decimal("50.00")


def _eur(amount: Decimal | str | int) -> Money:
    return Money(Decimal(str(amount)), "EUR")


def _order(payment_status=PaymentStatus.PENDING, **kwargs):
    """An order that owes ``DUE``, with no line items (``amount_due`` is a
    snapshot, so the lines do not matter here)."""
    kwargs.setdefault("pay_way", PayWayFactory.create_online_payment("stripe"))
    kwargs.setdefault("status", OrderStatus.PENDING)
    kwargs.setdefault("amount_due", _eur(DUE))
    return OrderFactory(
        num_order_items=0, payment_status=payment_status, **kwargs
    )


def _breakdown(order) -> dict:
    return OrderDetailSerializer().get_pricing_breakdown(order)


class TestAnUnpaidOnlineOrder:
    def test_still_owes_the_whole_amount_due(self):
        order = _order()

        breakdown = _breakdown(order)

        assert order.remaining_amount == _eur(DUE)
        assert breakdown["amount_due"] == DUE
        assert breakdown["remaining_amount"] == DUE

    @pytest.mark.parametrize(
        "payment_status", [PaymentStatus.PROCESSING, PaymentStatus.FAILED]
    )
    def test_so_does_one_whose_payment_is_not_settled(self, payment_status):
        assert _breakdown(_order(payment_status))["remaining_amount"] == DUE

    def test_the_breakdown_does_not_report_it_as_paid(self):
        assert "paid_amount" not in _breakdown(_order())


class TestAPaidOnlineOrder:
    def test_owes_nothing_and_keeps_its_amount_due(self):
        order = _order(PaymentStatus.COMPLETED)
        PaymentTransactionFactory(order=order, amount=_eur(DUE))

        breakdown = _breakdown(order)

        assert order.remaining_amount == _eur(0)
        assert breakdown["amount_due"] == DUE
        assert breakdown["remaining_amount"] == 0

    def test_a_payment_settled_by_hand_owes_nothing_with_no_ledger_row(self):
        """Staff marked it paid, or cash was collected under a payment
        method the ledger backfill does not key on: no provider, so no
        CHARGE or COD_COLLECTION row."""
        order = _order(PaymentStatus.COMPLETED)

        assert not order.payment_transactions.exists()
        assert _breakdown(order)["remaining_amount"] == 0

    def test_an_order_deductions_covered_in_full_owes_nothing(self):
        order = _order(PaymentStatus.COMPLETED, amount_due=_eur(0))

        assert _breakdown(order)["remaining_amount"] == 0


class TestARefundedOrder:
    def test_a_partial_refund_does_not_create_an_amount_owed(self):
        order = _order(PaymentStatus.COMPLETED)
        charge = PaymentTransactionFactory(order=order, amount=_eur(DUE))

        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("20.00"), reference="re_part"
        )
        order.refresh_from_db()

        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        assert _breakdown(order)["amount_due"] == DUE
        assert _breakdown(order)["remaining_amount"] == 0

    def test_a_full_refund_owes_nothing(self):
        order = _order(PaymentStatus.COMPLETED)
        charge = PaymentTransactionFactory(order=order, amount=_eur(DUE))

        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur(DUE), reference="re_full"
        )
        order.refresh_from_db()

        assert order.payment_status == PaymentStatus.REFUNDED
        assert _breakdown(order)["remaining_amount"] == 0


class TestACanceledOrder:
    def test_owes_nothing(self):
        """It closed unpaid, so no payment will ever be collected."""
        order = _order(PaymentStatus.CANCELED, status=OrderStatus.CANCELED)

        assert _breakdown(order)["amount_due"] == DUE
        assert _breakdown(order)["remaining_amount"] == 0


class TestACashOnDeliveryOrder:
    @staticmethod
    def _cod_order():
        return _order(
            pay_way=PayWayFactory.create_offline_payment(
                provider_code="cash_on_delivery", requires_confirmation=False
            )
        )

    def test_owes_its_amount_due_until_the_courier_collects(self):
        order = self._cod_order()

        breakdown = _breakdown(order)

        assert breakdown["amount_due"] == DUE
        assert breakdown["remaining_amount"] == DUE

    def test_owes_nothing_once_the_courier_has_collected(self):
        order = self._cod_order()

        settled = OrderService.settle_cod_payment(
            order,
            amount=_eur(DUE),
            provider="acs_cod",
            reference=f"order-{order.pk}",
        )
        order.refresh_from_db()

        assert settled is True
        assert order.payment_status == PaymentStatus.COMPLETED
        assert _breakdown(order)["remaining_amount"] == 0


def test_it_reads_the_order_row_and_nothing_else(django_assert_num_queries):
    """No ledger, no aggregate: ``remaining_amount`` is two attributes."""
    order = _order()

    with django_assert_num_queries(0):
        assert order.remaining_amount == _eur(DUE)
