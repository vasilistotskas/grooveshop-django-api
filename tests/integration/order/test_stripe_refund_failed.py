"""A Stripe refund that was accepted and later FAILED.

Stripe books a refund as ``succeeded`` or ``pending`` and can move it to
``failed`` afterwards, returning the money to the merchant's Stripe balance
(docs.stripe.com/refunds). The ledger counted it when it was accepted, so a
``REFUND_REVERSAL`` row puts the money back as held, ``payment_status`` is
derived again, and staff are told.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from djmoney.money import Money
from djstripe.models import Event

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory, PaymentTransactionFactory
from order.models.history import OrderHistory
from order.payment_ledger import PaymentLedger
from order.services import OrderService
from order.signals.handlers import (
    handle_stripe_charge_refunded,
    handle_stripe_refund_failed,
)
from pay_way.factories import PayWayFactory
from tests.utils.stripe_refunds import FakeStripeRefunds

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

REVERSAL = PaymentTransactionKind.REFUND_REVERSAL


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def _order(captured="100.00"):
    order = OrderFactory(
        num_order_items=0,
        pay_way=PayWayFactory.create_online_payment(),
        payment_id="pi_failed",
        status=OrderStatus.DELIVERED,
        payment_status=PaymentStatus.COMPLETED,
        amount_due=_eur(captured),
    )
    PaymentTransactionFactory(
        order=order, amount=_eur(captured), provider_reference="pi_failed"
    )
    return order


def _event(
    refund_id: str,
    amount: int,
    *,
    status: str = "failed",
    kind: str = "refund.failed",
    payment_intent: str = "pi_failed",
) -> Mock:
    event = Mock(spec=Event)
    event.id = f"evt_{kind}_{refund_id}"
    event.type = kind
    event.data = {
        "object": {
            "id": refund_id,
            "object": "refund",
            "status": status,
            "amount": amount,
            "currency": "eur",
            "payment_intent": payment_intent,
            "failure_reason": "expired_or_canceled_card",
        }
    }
    return event


def _deliver(event: Mock) -> None:
    handle_stripe_refund_failed(sender=None, event=event)


@pytest.fixture
def alerts():
    with patch("order.signals.handlers.send_ops_alert") as send:
        yield send


def _status(order) -> str:
    order.refresh_from_db()
    return order.payment_status


def _notes(order) -> list[str]:
    return [
        row.new_value["note"]
        for row in OrderHistory.objects.filter(order=order, change_type="NOTE")
    ]


class TestAFailedRefundIsReversed:
    def test_a_fully_refunded_order_is_back_to_paid(self, alerts):
        order = _order()
        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order)
        assert _status(order) == PaymentStatus.REFUNDED
        [re_1] = [c for c in stripe_api.created]

        _deliver(_event(re_1.id, 10000))

        assert _status(order) == PaymentStatus.COMPLETED
        assert PaymentLedger.refunded(order) == 0
        assert PaymentLedger.refundable(order) == Decimal("100.00")
        [row] = order.payment_transactions.filter(kind=REVERSAL)
        assert row.provider_reference == re_1.id
        assert row.amount == _eur("100.00")

    def test_one_of_two_refunds_failing_leaves_the_other_standing(self, alerts):
        order = _order()
        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order, amount=_eur("30.00"))
            OrderService.refund_order(order, amount=_eur("20.00"))
        second = stripe_api.created[1]

        _deliver(_event(second.id, 2000))

        assert _status(order) == PaymentStatus.PARTIALLY_REFUNDED
        assert PaymentLedger.refunded(order) == Decimal("30.00")
        assert PaymentLedger.refundable(order) == Decimal("70.00")

    def test_staff_are_told_on_the_order_and_by_email(self, alerts):
        order = _order()
        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order)

        _deliver(_event(stripe_api.created[0].id, 10000))

        [note] = [n for n in _notes(order) if "FAILED" in n]
        assert "expired_or_canceled_card" in note
        assert "pay them back another way" in note
        alerts.assert_called_once()
        assert alerts.call_args.kwargs["kind"].value == "refund_failed"
        assert "FAILED" in alerts.call_args.kwargs["message"]

    def test_a_dashboard_refund_booked_from_the_charge_event_is_reversed(
        self, alerts
    ):
        """No refund id on the ledger row (``charge.refunded`` books by
        event id): the reversal is by amount, keyed on the refund id."""
        order = _order()
        charge_event = Mock(spec=Event)
        charge_event.id = "evt_charge_refunded"
        charge_event.data = {
            "object": {
                "id": "ch_x",
                "payment_intent": "pi_failed",
                "amount": 10000,
                "amount_refunded": 4000,
                "currency": "eur",
            }
        }
        handle_stripe_charge_refunded(sender=None, event=charge_event)
        assert _status(order) == PaymentStatus.PARTIALLY_REFUNDED

        _deliver(_event("re_dashboard", 4000))

        assert _status(order) == PaymentStatus.COMPLETED
        assert PaymentLedger.refundable(order) == Decimal("100.00")


class TestOnlyAFailureCounts:
    def test_a_refund_that_succeeded_changes_nothing(self, alerts):
        order = _order()
        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order)

        _deliver(
            _event(
                stripe_api.created[0].id,
                10000,
                status="succeeded",
                kind="refund.updated",
            )
        )

        assert _status(order) == PaymentStatus.REFUNDED
        assert not order.payment_transactions.filter(kind=REVERSAL).exists()
        alerts.assert_not_called()

    def test_the_three_events_and_a_redelivery_reverse_it_once(self, alerts):
        order = _order()
        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order, amount=_eur("60.00"))
        refund_id = stripe_api.created[0].id

        for kind in (
            "refund.failed",
            "refund.updated",
            "charge.refund.updated",
            "refund.failed",
        ):
            _deliver(_event(refund_id, 6000, kind=kind))

        assert order.payment_transactions.filter(kind=REVERSAL).count() == 1
        assert PaymentLedger.refunded(order) == 0
        alerts.assert_called_once()

    def test_a_refund_the_ledger_never_counted_is_ignored(self, alerts):
        order = _order()

        _deliver(_event("re_unknown", 5000))

        assert _status(order) == PaymentStatus.COMPLETED
        assert not order.payment_transactions.filter(kind=REVERSAL).exists()
        alerts.assert_not_called()

    def test_an_unknown_payment_is_ignored(self, alerts):
        _deliver(_event("re_x", 5000, payment_intent="pi_nobody"))

        alerts.assert_not_called()


class TestTheLedgerKeepsAddingUpAfterAReversal:
    def test_a_failed_refund_can_be_refunded_again(self, alerts):
        order = _order()
        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order)
            _deliver(_event(stripe_api.created[0].id, 10000))
            result = OrderService.refund_order(order)

        assert result.refunded == _eur("100.00")
        assert _status(order) == PaymentStatus.REFUNDED
        assert PaymentLedger.refunded(order) == Decimal("100.00")
