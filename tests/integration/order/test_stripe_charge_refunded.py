"""The Stripe ``charge.refunded`` webhook against the payment ledger.

The event carries the charge's CUMULATIVE ``amount_refunded`` and no refund
id, so the handler brings the ledger up to that figure: what the ledger
does not hold yet becomes one REFUND row (keyed on the event id) and
``PaymentLedger.record_refund`` derives ``payment_status`` from the sums,
exactly as for the in-app refund and the Viva webhook.
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
from order.models import OrderHistory
from order.services import OrderService
from order.signals.handlers import handle_stripe_charge_refunded
from tests.utils.payments import CAPTURED

pytestmark = pytest.mark.django_db


def _event(
    *,
    event_id: str,
    payment_intent_id: str,
    amount: int,
    amount_refunded: int,
    currency: str = "eur",
) -> Mock:
    event = Mock(spec=Event)
    event.id = event_id
    event.type = "charge.refunded"
    event.data = {
        "object": {
            "id": "ch_test_xxx",
            "payment_intent": payment_intent_id,
            "amount": amount,
            "amount_refunded": amount_refunded,
            "currency": currency,
            "refunded": amount_refunded >= amount > 0,
        }
    }
    return event


def _order(payment_intent_id: str, **kwargs):
    return OrderFactory(
        num_order_items=0,
        payment_id=payment_intent_id,
        status=OrderStatus.DELIVERED,
        payment_status=PaymentStatus.COMPLETED,
        **kwargs,
    )


def _refunds(order):
    return order.payment_transactions.filter(kind=PaymentTransactionKind.REFUND)


def _deliver(event_id, payment_intent_id, amount_refunded, amount=5000):
    handle_stripe_charge_refunded(
        sender=None,
        event=_event(
            event_id=event_id,
            payment_intent_id=payment_intent_id,
            amount=amount,
            amount_refunded=amount_refunded,
        ),
    )


class TestChargeRefundedWebhook:
    def test_full_refund_flips_payment_status_to_refunded(self):
        order = _order("pi_full_refund_1")

        _deliver("evt_full_refund_1", "pi_full_refund_1", 5000)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        [charge] = order.payment_transactions.filter(
            kind=PaymentTransactionKind.CHARGE
        )
        assert charge.provider == "stripe"
        assert charge.provider_reference == "pi_full_refund_1"
        assert charge.amount == CAPTURED
        [refund] = _refunds(order)
        assert refund.provider_reference == "evt_full_refund_1"
        assert refund.amount == CAPTURED
        assert refund.charge == charge

    def test_the_refund_is_written_to_the_order_history(self):
        order = _order("pi_history_1")

        _deliver("evt_history_1", "pi_history_1", 1500)

        row = OrderHistory.objects.filter(
            order=order, change_type="PAYMENT"
        ).latest("id")
        assert row.previous_value["payment_status"] == "COMPLETED"
        assert row.new_value["payment_status"] == "PARTIALLY_REFUNDED"
        assert row.new_value["is_full_refund"] is False

    @pytest.mark.parametrize(
        ("currency", "expected"),
        [
            # A charge reports minor units in the currency's OWN exponent:
            # 5000 of a two-decimal currency is 50, of a zero-decimal one
            # it is 5000.
            ("eur", Money(Decimal(50), "EUR")),
            ("jpy", Money(Decimal(5000), "JPY")),
        ],
    )
    def test_full_refund_says_how_much_was_refunded(
        self, currency, expected, refunded_signals
    ):
        """``order_refunded`` always carries its amount: a receiver must
        never have to read its absence as "everything"."""
        _order(f"pi_full_refund_amount_{currency}")

        handle_stripe_charge_refunded(
            sender=None,
            event=_event(
                event_id=f"evt_full_refund_amount_{currency}",
                payment_intent_id=f"pi_full_refund_amount_{currency}",
                amount=5000,
                amount_refunded=5000,
                currency=currency,
            ),
        )

        assert [r["amount"] for r in refunded_signals] == [expected]

    def test_partial_refund_flips_payment_status_to_partially_refunded(
        self, refunded_signals
    ):
        order = _order("pi_partial_refund_1")

        _deliver("evt_partial_refund_1", "pi_partial_refund_1", 1500)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        [refund] = _refunds(order)
        assert refund.amount == Money(Decimal("15.00"), "EUR")
        # A partial refund is signalled like any other: the customer is
        # told, Meta hears of it. What hangs off the order's total is
        # released only at REFUNDED (the gift-card and loyalty receivers).
        assert [r["amount"] for r in refunded_signals] == [
            Money(Decimal("15.00"), "EUR")
        ]

    def test_redelivery_is_idempotent(self, refunded_signals):
        order = _order("pi_idempotent_1")

        _deliver("evt_idempotent_1", "pi_idempotent_1", 5000)
        # Replay the same event: the ledger already holds the amount.
        _deliver("evt_idempotent_1", "pi_idempotent_1", 5000)

        assert _refunds(order).count() == 1
        assert len(refunded_signals) == 1

    def test_each_event_adds_only_what_the_ledger_does_not_hold(
        self, refunded_signals
    ):
        order = _order("pi_cumulative_1")

        _deliver("evt_cumulative_1", "pi_cumulative_1", 1500)
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        _deliver("evt_cumulative_2", "pi_cumulative_1", 5000)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        assert [r.amount.amount for r in _refunds(order)] == [
            Decimal("15.00"),
            Decimal("35.00"),
        ]
        assert [r["amount"].amount for r in refunded_signals] == [
            Decimal("15.00"),
            Decimal("35.00"),
        ]

    def test_an_older_event_arriving_late_adds_nothing(self, refunded_signals):
        """Stripe does not order its events: the one reporting 15 can land
        after the one reporting 50, and must not move REFUNDED back."""
        order = _order("pi_late_1")

        _deliver("evt_late_2", "pi_late_1", 5000)
        _deliver("evt_late_1", "pi_late_1", 1500)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        assert [r.amount.amount for r in _refunds(order)] == [Decimal("50.00")]
        assert len(refunded_signals) == 1

    def test_the_refund_this_app_made_is_not_counted_again(
        self, refunded_signals
    ):
        """Stripe also notifies the refund ``refund_order`` just made, under
        a refund id of its own; the ledger already holds it."""
        order = _order("pi_in_app_1", paid_amount=Money("50.00", "EUR"))
        PaymentTransactionFactory(
            order=order,
            amount=Money("50.00", "EUR"),
            provider_reference="pi_in_app_1",
        )
        with patch("order.payment.get_payment_provider") as get_provider:
            get_provider.return_value.refund_payment.return_value = (
                True,
                {"refund_id": "re_in_app_1", "amount": "15.00"},
            )
            OrderService.refund_order(order, amount=Money("15.00", "EUR"))
        assert len(refunded_signals) == 1

        _deliver("evt_in_app_1", "pi_in_app_1", 1500)

        assert [r.provider_reference for r in _refunds(order)] == [
            "re_in_app_1"
        ]
        assert len(refunded_signals) == 1
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED

    def test_a_dashboard_refund_next_to_an_in_app_one_counts_each_once(self):
        order = _order("pi_mixed_1", paid_amount=Money("50.00", "EUR"))
        PaymentTransactionFactory(
            order=order,
            amount=Money("50.00", "EUR"),
            provider_reference="pi_mixed_1",
        )

        _deliver("evt_mixed_dashboard", "pi_mixed_1", 500)
        with patch("order.payment.get_payment_provider") as get_provider:
            get_provider.return_value.refund_payment.return_value = (
                True,
                {"refund_id": "re_mixed_in_app", "amount": "5.00"},
            )
            OrderService.refund_order(order, amount=Money("5.00", "EUR"))
        _deliver("evt_mixed_in_app", "pi_mixed_1", 1000)

        assert [r.amount.amount for r in _refunds(order)] == [
            Decimal("5.00"),
            Decimal("5.00"),
        ]

    def test_unknown_payment_intent_silently_warns(self):
        # Must not raise.
        _deliver("evt_unknown_1", "pi_does_not_exist", 5000)

    def test_event_without_payment_intent_id_no_ops(self):
        event = Mock(spec=Event)
        event.id = "evt_no_pi"
        event.type = "charge.refunded"
        event.data = {
            "object": {
                "id": "ch_no_pi",
                "payment_intent": "",
                "amount": 5000,
                "amount_refunded": 5000,
                "currency": "eur",
            }
        }

        handle_stripe_charge_refunded(sender=None, event=event)

    def test_a_late_payment_succeeded_cannot_undo_the_refund(self):
        """The refund lands in a settled status, so the success event that
        arrives after it is stale and changes nothing."""
        order = _order("pi_late_success_1")
        _deliver("evt_late_success_1", "pi_late_success_1", 1500)

        OrderService.handle_payment_succeeded(
            "pi_late_success_1", captured=CAPTURED
        )

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        assert (
            order.payment_transactions.filter(
                kind=PaymentTransactionKind.CHARGE
            ).count()
            == 1
        )

    def test_full_refund_dispatches_confirmation_email_once(self):
        """PR #8 U1: a charge.refunded event for a full refund queues the
        customer's refund-confirmation email exactly once. A redelivery
        adds nothing to the ledger, so it never reaches the signal."""
        order = _order("pi_email_once_1")

        # The handler dispatches via apply_async (not .delay) so it can
        # pin the tenant schema in the task header — see the on_commit
        # schema-capture pattern in handle_order_refunded.
        with patch(
            "order.signals.handlers.send_refund_confirmation_email.apply_async"
        ) as mock_email:
            _deliver("evt_email_once_1", "pi_email_once_1", 5000)
            # Stripe redelivers the same event ~10s later.
            _deliver("evt_email_once_1", "pi_email_once_1", 5000)

        assert mock_email.call_count == 1
        # The task is queued for the refund's own ledger row.
        [refund] = _refunds(order)
        mock_email.assert_called_with(
            args=[refund.pk], headers={"_schema_name": "public"}
        )
