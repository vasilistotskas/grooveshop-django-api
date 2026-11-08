"""The Stripe ``refund.created`` / ``refund.updated`` webhook against the
payment ledger.

An order's refund is booked once per Stripe refund id (``re_...``), the key
the in-app refund, the reconciliation lookup and ``refund.failed`` use, so a
refund is one row however it arrives and a later reversal finds it.
``charge.refunded`` carries only a cumulative figure and books no order
refund.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import stripe
from djmoney.money import Money
from djstripe.models import Event

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory, PaymentTransactionFactory
from order.models import OrderHistory
from order.models.refund_attempt import RefundAttemptStatus
from order.payment import REFUND_ATTEMPT_METADATA_KEY
from order.payment_ledger import PaymentLedger
from order.services import OrderService
from order.signals.handlers import (
    handle_stripe_charge_refunded,
    handle_stripe_refund_failed,
    handle_stripe_refund_made,
)
from tests.utils.payments import CAPTURED
from tests.utils.stripe_refunds import FakeStripeRefunds, refund_event

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("refund_states"),
]


def _order(payment_intent_id: str, **kwargs):
    order = OrderFactory(
        num_order_items=0,
        payment_id=payment_intent_id,
        status=OrderStatus.DELIVERED,
        payment_status=PaymentStatus.COMPLETED,
        **kwargs,
    )
    PaymentTransactionFactory(
        order=order,
        amount=CAPTURED,
        provider="stripe",
        provider_reference=payment_intent_id,
    )
    return order


def _refunds(order):
    return order.payment_transactions.filter(kind=PaymentTransactionKind.REFUND)


def _deliver(refund_id, payment_intent_id, cents, **kwargs):
    handle_stripe_refund_made(
        sender=None,
        event=refund_event(refund_id, payment_intent_id, cents, **kwargs),
    )


class TestRefundWebhook:
    def test_full_refund_flips_payment_status_to_refunded(self):
        order = _order("pi_full_refund_1")

        _deliver("re_full_1", "pi_full_refund_1", 5000)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        [refund] = _refunds(order)
        assert refund.provider == "stripe"
        assert refund.provider_reference == "re_full_1"
        assert refund.amount == CAPTURED
        assert refund.charge.provider_reference == "pi_full_refund_1"

    def test_the_refund_is_written_to_the_order_history(self):
        order = _order("pi_history_1")

        _deliver("re_history_1", "pi_history_1", 1500)

        row = OrderHistory.objects.filter(
            order=order, change_type="PAYMENT"
        ).latest("id")
        assert row.previous_value["payment_status"] == "COMPLETED"
        assert row.new_value["payment_status"] == "PARTIALLY_REFUNDED"
        assert row.new_value["is_full_refund"] is False

    @pytest.mark.parametrize(
        ("currency", "expected"),
        [
            # A refund reports minor units in the currency's OWN exponent:
            # 5000 of a two-decimal currency is 50, of a zero-decimal one
            # it is 5000.
            ("eur", Money(Decimal(50), "EUR")),
            ("jpy", Money(Decimal(5000), "JPY")),
        ],
    )
    def test_the_signal_says_how_much_was_refunded(
        self, currency, expected, refunded_signals
    ):
        """``order_refunded`` always carries its amount: a receiver must
        never have to read its absence as "everything"."""
        pi = f"pi_amount_{currency}"
        order = OrderFactory(
            num_order_items=0,
            payment_id=pi,
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.COMPLETED,
        )
        PaymentTransactionFactory(
            order=order,
            amount=expected,
            provider="stripe",
            provider_reference=pi,
        )

        _deliver(f"re_amount_{currency}", pi, 5000, currency=currency)

        assert [r["amount"] for r in refunded_signals] == [expected]

    def test_partial_refund_flips_payment_status_to_partially_refunded(
        self, refunded_signals
    ):
        order = _order("pi_partial_refund_1")

        _deliver("re_partial_1", "pi_partial_refund_1", 1500)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        [refund] = _refunds(order)
        assert refund.amount == Money(Decimal("15.00"), "EUR")
        assert [r["amount"] for r in refunded_signals] == [
            Money(Decimal("15.00"), "EUR")
        ]

    def test_redelivery_and_the_updated_event_book_the_refund_once(
        self, refunded_signals
    ):
        order = _order("pi_idempotent_1")

        _deliver("re_idem_1", "pi_idempotent_1", 5000)
        _deliver("re_idem_1", "pi_idempotent_1", 5000)
        _deliver(
            "re_idem_1",
            "pi_idempotent_1",
            5000,
            event_type="refund.updated",
        )

        assert _refunds(order).count() == 1
        assert len(refunded_signals) == 1

    def test_a_late_created_event_for_a_refund_that_failed_books_nothing(
        self, refunded_signals
    ):
        """``refund.created`` says ``pending``; by the time it is processed
        Stripe reports the refund failed: its status NOW decides."""
        order = _order("pi_late_created_1")

        _deliver(
            "re_late_created",
            "pi_late_created_1",
            1500,
            status="pending",
            current="failed",
        )

        assert not _refunds(order).exists()
        assert refunded_signals == []

    def test_an_updated_event_that_reports_succeeded_books_once(
        self, refunded_signals
    ):
        order = _order("pi_updated_1")

        for _ in range(2):
            _deliver(
                "re_updated_1",
                "pi_updated_1",
                1500,
                status="succeeded",
                event_type="refund.updated",
            )

        assert [r.provider_reference for r in _refunds(order)] == [
            "re_updated_1"
        ]
        assert len(refunded_signals) == 1

    def test_a_stripe_error_reading_the_refund_propagates_for_redelivery(self):
        order = _order("pi_error_1")

        with (
            patch(
                "order.payment.stripe.Refund.retrieve",
                side_effect=stripe.APIConnectionError("down"),
            ),
            pytest.raises(stripe.APIConnectionError),
        ):
            _deliver("re_error_1", "pi_error_1", 1500)

        assert not _refunds(order).exists()

    def test_the_attempt_named_in_the_metadata_is_the_one_settled(self):
        """Two attempts of one amount are PENDING: the refund carries the
        key of the SECOND, so it settles that one, not the oldest."""
        from order.models.refund_attempt import RefundAttempt

        order = _order("pi_key_1")
        charge = order.payment_transactions.get()
        first, second = (
            RefundAttempt.objects.create(
                order=order, charge=charge, amount=Money("20.00", "EUR")
            )
            for _ in range(2)
        )

        _deliver(
            "re_key_1",
            "pi_key_1",
            2000,
            metadata={REFUND_ATTEMPT_METADATA_KEY: second.provider_key},
        )

        first.refresh_from_db()
        second.refresh_from_db()
        assert second.status == RefundAttemptStatus.SETTLED
        assert second.refund == _refunds(order).get()
        assert first.status == RefundAttemptStatus.PENDING

    def test_a_refund_without_a_key_settles_the_oldest_matching_attempt(self):
        from order.models.refund_attempt import RefundAttempt

        order = _order("pi_key_2")
        charge = order.payment_transactions.get()
        first, second = (
            RefundAttempt.objects.create(
                order=order, charge=charge, amount=Money("20.00", "EUR")
            )
            for _ in range(2)
        )

        _deliver("re_key_2", "pi_key_2", 2000)

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.status == RefundAttemptStatus.SETTLED
        assert second.status == RefundAttemptStatus.PENDING

    def test_each_refund_is_its_own_row_under_its_own_id(
        self, refunded_signals
    ):
        order = _order("pi_two_1")

        _deliver("re_two_a", "pi_two_1", 1500)
        _deliver("re_two_b", "pi_two_1", 3500)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        assert [
            (r.provider_reference, r.amount.amount) for r in _refunds(order)
        ] == [("re_two_a", Decimal("15.00")), ("re_two_b", Decimal("35.00"))]
        assert len(refunded_signals) == 2

    def test_the_order_of_two_events_does_not_matter(self):
        order = _order("pi_late_1")

        _deliver("re_late_b", "pi_late_1", 3500)
        _deliver("re_late_a", "pi_late_1", 1500)

        assert sorted(r.amount.amount for r in _refunds(order)) == [
            Decimal("15.00"),
            Decimal("35.00"),
        ]

    @pytest.mark.parametrize(
        "status", ["failed", "canceled", "requires_action"]
    )
    def test_a_refund_that_returned_nothing_books_nothing(
        self, status, refunded_signals
    ):
        order = _order("pi_status_1")

        _deliver("re_status_1", "pi_status_1", 1500, status=status)

        assert not _refunds(order).exists()
        assert refunded_signals == []

    def test_the_refund_this_app_made_is_not_counted_again(
        self, refunded_signals
    ):
        """Stripe also notifies the refund ``refund_order`` just made, under
        its refund id; the ledger already holds it."""
        order = _order("pi_in_app_1", amount_due=Money("50.00", "EUR"))
        with patch("order.payment.get_payment_provider") as get_provider:
            get_provider.return_value.refund_payment.return_value = (
                True,
                {"refund_id": "re_in_app_1", "amount": "15.00"},
            )
            OrderService.refund_order(order, amount=Money("15.00", "EUR"))
        assert len(refunded_signals) == 1

        _deliver("re_in_app_1", "pi_in_app_1", 1500)

        assert [r.provider_reference for r in _refunds(order)] == [
            "re_in_app_1"
        ]
        assert len(refunded_signals) == 1

    def test_a_dashboard_refund_next_to_an_in_app_one_counts_each_once(self):
        order = _order("pi_mixed_1", amount_due=Money("50.00", "EUR"))

        _deliver("re_mixed_dashboard", "pi_mixed_1", 500)
        with patch("order.payment.get_payment_provider") as get_provider:
            get_provider.return_value.refund_payment.return_value = (
                True,
                {"refund_id": "re_mixed_in_app", "amount": "5.00"},
            )
            OrderService.refund_order(order, amount=Money("5.00", "EUR"))
        _deliver("re_mixed_in_app", "pi_mixed_1", 500)

        assert [r.amount.amount for r in _refunds(order)] == [
            Decimal("5.00"),
            Decimal("5.00"),
        ]

    def test_unknown_payment_intent_silently_warns(self):
        # Must not raise.
        _deliver("re_unknown_1", "pi_does_not_exist", 5000)

    def test_a_refund_without_a_payment_intent_no_ops(self):
        _deliver("re_no_pi", "", 5000)

    def test_a_late_payment_succeeded_cannot_undo_the_refund(self):
        order = _order("pi_late_success_1")
        _deliver("re_late_success_1", "pi_late_success_1", 1500)

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
        order = _order("pi_email_once_1")

        with patch(
            "order.signals.handlers.send_refund_confirmation_email.apply_async"
        ) as mock_email:
            _deliver("re_email_once_1", "pi_email_once_1", 5000)
            _deliver("re_email_once_1", "pi_email_once_1", 5000)

        assert mock_email.call_count == 1
        [refund] = _refunds(order)
        mock_email.assert_called_with(
            args=[refund.pk], headers={"_schema_name": "public"}
        )

    def test_charge_refunded_books_no_order_refund(self):
        """Its figure is cumulative and names no refund: the refund events
        are the only booking, so the two can never count a refund twice."""
        order = _order("pi_charge_refunded_1")
        event = Mock(spec=Event)
        event.id = "evt_charge_refunded_1"
        event.type = "charge.refunded"
        event.data = {
            "object": {
                "id": "ch_x",
                "payment_intent": "pi_charge_refunded_1",
                "amount": 5000,
                "amount_refunded": 5000,
                "currency": "eur",
            }
        }

        handle_stripe_charge_refunded(sender=None, event=event)
        _deliver("re_cr_1", "pi_charge_refunded_1", 5000)

        assert _refunds(order).count() == 1


class TestAWebhookRefundIsKeyedLikeEveryOtherPath:
    """The refund id is the one key: reconciliation sees the row the
    webhook booked, and a reversal finds the row to free."""

    def test_reconciliation_does_not_book_it_a_second_time(self):
        from order.refund_reconciliation import reconcile

        order = _order("pi_keyed_1", amount_due=Money("50.00", "EUR"))
        with FakeStripeRefunds(fail_with=stripe.APIConnectionError("x")):
            OrderService.refund_order(order, amount=Money("20.00", "EUR"))
        [attempt] = order.refund_attempts.all()
        assert attempt.status == RefundAttemptStatus.PENDING

        # Stripe made refund re_1 for the attempt, and its webhook arrives
        # first: it is booked and settles the matching attempt.
        with FakeStripeRefunds() as stripe_api:
            stripe_api.created.append(
                SimpleNamespace(
                    id="re_keyed",
                    status="succeeded",
                    amount=2000,
                    currency="eur",
                    payment_intent="pi_keyed_1",
                    metadata={
                        REFUND_ATTEMPT_METADATA_KEY: attempt.provider_key
                    },
                )
            )
            _deliver("re_keyed", "pi_keyed_1", 2000)
            attempt.refresh_from_db()
            assert attempt.status == RefundAttemptStatus.SETTLED
            reconcile(attempt)

        assert [r.provider_reference for r in _refunds(order)] == ["re_keyed"]
        assert PaymentLedger.refunded(order) == Decimal("20.00")

    def test_a_failed_refund_frees_the_credit_of_a_webhook_booked_one(
        self, refund_states
    ):
        from order import credit_allocation

        order = _order("pi_keyed_2", amount_due=Money("50.00", "EUR"))
        _deliver("re_keyed_2", "pi_keyed_2", 2000)
        refund = _refunds(order).get()
        refund_states["re_keyed_2"] = "failed"

        failed = Mock(spec=Event)
        failed.id = "evt_failed_keyed_2"
        failed.type = "refund.failed"
        failed.data = {
            "object": {
                "id": "re_keyed_2",
                "object": "refund",
                "status": "failed",
                "amount": 2000,
                "currency": "eur",
                "payment_intent": "pi_keyed_2",
            }
        }
        with patch("order.signals.handlers.send_email"):
            handle_stripe_refund_failed(sender=None, event=failed)

        assert credit_allocation.reversed_of(refund) == Decimal("20.00")
