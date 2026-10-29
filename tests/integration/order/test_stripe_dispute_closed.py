"""A lost Stripe dispute against the payment ledger.

``charge.dispute.created`` only flags the order: the money is still the
merchant's to defend. When ``charge.dispute.closed`` reports ``lost`` the
money is gone, so it is ONE CHARGEBACK row keyed on the dispute id. It
lowers what can be refunded and leaves ``payment_status`` alone, and it is
not a refund: no ``order_refunded``, no customer email. The only side effect
beyond history and the staff email is the loyalty reversal, once refunds and
chargebacks together cover everything received.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.urls import reverse
from djmoney.money import Money
from djstripe.models import Event

from loyalty.signals import handle_order_refunded_loyalty
from order.admin import ORDER_HISTORY_CHANGE_TYPE_VARIANT
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory, PaymentTransactionFactory
from order.models import OrderHistory, RefundNotification
from order.payment_ledger import PaymentLedger
from order.signals import order_charged_back
from order.signals.handlers import (
    handle_stripe_charge_refunded,
    handle_stripe_dispute_closed,
    handle_stripe_dispute_created,
)
from user.factories import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

PI = "pi_dispute_1"
DISPUTE = "dp_test_1"


def _event(
    event_id: str,
    *,
    status: str = "lost",
    amount: int = 2000,
    event_type: str = "charge.dispute.closed",
    payment_intent: str = PI,
    dispute_id: str = DISPUTE,
) -> Mock:
    event = Mock(spec=Event)
    event.id = event_id
    event.type = event_type
    event.data = {
        "object": {
            "id": dispute_id,
            "charge": "ch_test_1",
            "payment_intent": payment_intent,
            "reason": "fraudulent",
            "status": status,
            "amount": amount,
            "currency": "eur",
        }
    }
    return event


def _paid_order(**kwargs):
    """An order paid with a 50.00 Stripe charge on the ledger."""
    order = OrderFactory(
        num_order_items=0,
        payment_id=PI,
        status=OrderStatus.DELIVERED,
        payment_status=PaymentStatus.COMPLETED,
        metadata={},
        **kwargs,
    )
    charge = PaymentTransactionFactory(
        order=order, provider="stripe", provider_reference=PI
    )
    return order, charge


def _chargebacks(order):
    return order.payment_transactions.filter(
        kind=PaymentTransactionKind.CHARGEBACK
    )


def _close(event):
    with patch(
        "order.signals.handlers.send_dispute_notification_email.apply_async"
    ) as email:
        handle_stripe_dispute_closed(sender=None, event=event)
    return email


@pytest.fixture
def chargeback_signals():
    received: list[dict] = []

    def receiver(sender, **kwargs):
        received.append(kwargs)

    order_charged_back.connect(receiver, weak=False)
    yield received
    order_charged_back.disconnect(receiver)


class TestDisputeOpened:
    def test_an_opened_dispute_writes_no_ledger_row(self):
        order, _charge = _paid_order()

        with patch(
            "order.signals.handlers.send_dispute_notification_email.apply_async"
        ):
            handle_stripe_dispute_created(
                sender=None,
                event=_event("evt_open_1", event_type="charge.dispute.created"),
            )

        order.refresh_from_db()
        assert order.metadata["disputed"] is True
        assert not _chargebacks(order).exists()
        assert PaymentLedger.refundable(order) == Decimal("50.00")


class TestDisputeLost:
    def test_records_one_chargeback_against_the_charge(self):
        order, charge = _paid_order()

        _close(_event("evt_lost_1"))

        [row] = _chargebacks(order)
        assert row.provider == "stripe"
        assert row.provider_reference == DISPUTE
        assert row.amount == Money(Decimal("20.00"), "EUR")
        assert row.charge == charge

    def test_the_money_taken_back_is_no_longer_refundable(self):
        order, _charge = _paid_order()

        _close(_event("evt_lost_2"))

        assert PaymentLedger.refundable(order) == Decimal("30.00")
        assert PaymentLedger.refunded(order) == 0
        assert PaymentLedger.charged_back(order) == Decimal("20.00")

    def test_payment_status_does_not_move_and_nothing_is_refunded(
        self, refunded_signals
    ):
        order, _charge = _paid_order()

        _close(_event("evt_lost_3", amount=5000))

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert refunded_signals == []
        assert not RefundNotification.objects.filter(
            refund__order=order
        ).exists()
        assert PaymentLedger.refundable(order) == 0

    def test_staff_get_a_red_history_entry_and_one_email(self):
        order, _charge = _paid_order()

        email = _close(_event("evt_lost_4"))

        entry = OrderHistory.objects.get(
            order=order,
            change_type=OrderHistory.OrderHistoryChangeType.CHARGEBACK,
        )
        assert "20.00" in entry.description
        assert entry.new_value["dispute_id"] == DISPUTE
        assert ORDER_HISTORY_CHANGE_TYPE_VARIANT[entry.change_type] == "danger"
        email.assert_called_once()
        assert email.call_args.kwargs["args"] == [
            order.id,
            DISPUTE,
            "stripe",
        ]
        assert email.call_args.kwargs["kwargs"] == {"lost": True}

    def test_a_redelivered_event_adds_nothing(self, chargeback_signals):
        order, _charge = _paid_order()
        _close(_event("evt_lost_5"))

        email = _close(_event("evt_lost_5"))

        assert _chargebacks(order).count() == 1
        assert (
            OrderHistory.objects.filter(
                order=order,
                change_type=OrderHistory.OrderHistoryChangeType.CHARGEBACK,
            ).count()
            == 1
        )
        email.assert_not_called()
        assert len(chargeback_signals) == 1

    def test_another_event_for_the_same_dispute_adds_nothing(self):
        order, _charge = _paid_order()
        _close(_event("evt_lost_6"))

        email = _close(_event("evt_lost_6b"))

        assert _chargebacks(order).count() == 1
        assert (
            OrderHistory.objects.filter(
                order=order,
                change_type=OrderHistory.OrderHistoryChangeType.CHARGEBACK,
            ).count()
            == 1
        )
        email.assert_not_called()

    def test_a_charge_missing_from_the_ledger_is_read_from_the_provider(self):
        order = OrderFactory(
            num_order_items=0,
            payment_id=PI,
            payment_status=PaymentStatus.COMPLETED,
            metadata={},
        )

        with patch(
            "order.payment_ledger.PaymentLedger.ensure_charge",
            return_value=None,
        ):
            with pytest.raises(RuntimeError, match=DISPUTE):
                _close(_event("evt_lost_7"))

        assert not _chargebacks(order).exists()


class TestDisputeClosedOtherwise:
    def test_a_won_dispute_clears_the_flag_and_writes_no_row(self):
        order, _charge = _paid_order()
        with patch(
            "order.signals.handlers.send_dispute_notification_email.apply_async"
        ):
            handle_stripe_dispute_created(
                sender=None,
                event=_event("evt_open_2", event_type="charge.dispute.created"),
            )

        email = _close(_event("evt_won_1", status="won"))

        order.refresh_from_db()
        assert "disputed" not in order.metadata
        assert not _chargebacks(order).exists()
        assert PaymentLedger.refundable(order) == Decimal("50.00")
        email.assert_not_called()
        note = OrderHistory.objects.filter(order=order, change_type="NOTE")
        assert "status=won" in note.latest("id").new_value["note"]

    def test_a_redelivered_closing_event_writes_one_note(self):
        order, _charge = _paid_order()
        _close(_event("evt_won_2", status="won"))

        _close(_event("evt_won_2", status="won"))

        notes = OrderHistory.objects.filter(order=order, change_type="NOTE")
        assert sum("status=won" in n.new_value["note"] for n in notes) == 1

    def test_a_closed_inquiry_leaves_a_note_and_no_row(self):
        order, _charge = _paid_order()

        email = _close(_event("evt_warn_1", status="warning_closed"))

        assert not _chargebacks(order).exists()
        email.assert_not_called()
        order.refresh_from_db()
        assert "disputed" not in order.metadata
        note = OrderHistory.objects.filter(order=order, change_type="NOTE")
        assert "warning_closed" in note.latest("id").new_value["note"]


class TestChargebackIsCappedAtWhatIsLeft:
    def _refund(self, order, charge, amount):
        PaymentLedger.record_refund(
            order,
            charge=charge,
            amount=Money(Decimal(amount), "EUR"),
            reference=f"re_cap_{amount}",
        )

    def test_a_partly_refunded_payment_is_charged_back_for_what_is_left(self):
        order, charge = _paid_order()
        self._refund(order, charge, "30.00")

        email = _close(_event("evt_cap_1", amount=5000))

        [row] = _chargebacks(order)
        assert row.amount == Money(Decimal("20.00"), "EUR")
        assert PaymentLedger.refundable(order) == 0
        entry = OrderHistory.objects.get(
            order=order,
            change_type=OrderHistory.OrderHistoryChangeType.CHARGEBACK,
        )
        assert "20.00" in entry.description
        assert "disputed" in entry.description
        assert "30.00 already returned" in entry.description
        email.assert_called_once()
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED

    def test_a_fully_refunded_payment_records_no_row_but_staff_are_told(self):
        order, charge = _paid_order()
        self._refund(order, charge, "50.00")
        order.metadata = {"disputed": True, "dispute_id": DISPUTE}
        order.save(update_fields=["metadata"])

        email = _close(_event("evt_cap_2", amount=5000))

        assert not _chargebacks(order).exists()
        entry = OrderHistory.objects.get(
            order=order,
            change_type=OrderHistory.OrderHistoryChangeType.CHARGEBACK,
        )
        assert "not recorded" in entry.description
        email.assert_called_once()
        assert email.call_args.kwargs["kwargs"] == {"lost": True}
        order.refresh_from_db()
        assert "disputed" not in order.metadata

    def test_a_second_chargeback_cannot_take_what_the_first_took(self):
        order, charge = _paid_order()
        for reference, amount in (("dp_a", "30.00"), ("dp_b", "30.00")):
            PaymentLedger.record_chargeback(
                order,
                charge=charge,
                amount=Money(Decimal(amount), "EUR"),
                reference=reference,
            )

        assert [c.amount.amount for c in _chargebacks(order)] == [
            Decimal("30.00"),
            Decimal("20.00"),
        ]


class TestChargebackAmongRefunds:
    def test_charge_refunded_arithmetic_ignores_a_chargeback(self):
        order, charge = _paid_order()
        _close(_event("evt_lost_8"))
        assert PaymentLedger.refunded_on(charge) == 0

        event = Mock(spec=Event)
        event.id = "evt_refund_1"
        event.data = {
            "object": {
                "id": "ch_test_1",
                "payment_intent": PI,
                "amount": 5000,
                "amount_refunded": 1000,
                "currency": "eur",
            }
        }
        handle_stripe_charge_refunded(sender=None, event=event)

        assert PaymentLedger.refunded_on(charge) == Decimal("10.00")
        assert PaymentLedger.refunded(order) == Decimal("10.00")
        assert PaymentLedger.refundable(order) == Decimal("20.00")

    def test_a_partly_refunded_order_stays_partially_refunded(
        self, refunded_signals
    ):
        order, charge = _paid_order()
        PaymentLedger.record_refund(
            order,
            charge=charge,
            amount=Money(Decimal("10.00"), "EUR"),
            reference="re_1",
        )
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED

        _close(_event("evt_lost_9"))

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        assert refunded_signals == []

    def test_a_chargeback_row_must_name_its_charge(self):
        from django.db import IntegrityError, transaction

        order, _charge = _paid_order()

        with pytest.raises(IntegrityError), transaction.atomic():
            PaymentTransactionFactory(
                order=order,
                kind=PaymentTransactionKind.CHARGEBACK,
                provider_reference=DISPUTE,
            )


class TestLoyaltyReversal:
    """Points come back once refunds plus chargebacks cover the payment."""

    @pytest.fixture
    def queued(self):
        with patch("loyalty.signals.dispatch_on_commit") as dispatch:
            yield dispatch

    def _order_with_customer(self):
        user = UserAccountFactory(num_addresses=0)
        return _paid_order(user=user)

    def test_a_chargeback_covering_the_whole_payment_reverses(self, queued):
        order, _charge = self._order_with_customer()

        _close(_event("evt_loy_1", amount=5000))

        assert queued.call_count == 1
        assert queued.call_args.args[1] == [order.id]

    def test_refund_and_chargeback_together_cover_it(self, queued):
        order, charge = self._order_with_customer()
        PaymentLedger.record_refund(
            order,
            charge=charge,
            amount=Money(Decimal("10.00"), "EUR"),
            reference="re_2",
        )
        queued.assert_not_called()

        _close(_event("evt_loy_2", amount=4000))

        assert queued.call_count == 1

    def test_an_order_with_nothing_received_reverses_nothing(self, queued):
        user = UserAccountFactory(num_addresses=0)
        order = OrderFactory(
            num_order_items=0,
            user=user,
            payment_status=PaymentStatus.PARTIALLY_REFUNDED,
        )

        handle_order_refunded_loyalty(sender=type(order), order=order)

        queued.assert_not_called()

    def test_a_chargeback_that_does_not_cover_it_reverses_nothing(self, queued):
        self._order_with_customer()

        _close(_event("evt_loy_3", amount=2000))

        queued.assert_not_called()

    def test_a_refund_completing_the_cover_after_a_chargeback_reverses(
        self, queued
    ):
        order, charge = self._order_with_customer()
        _close(_event("evt_loy_4", amount=2000))
        queued.assert_not_called()

        PaymentLedger.record_refund(
            order,
            charge=charge,
            amount=Money(Decimal("30.00"), "EUR"),
            reference="re_3",
        )
        handle_order_refunded_loyalty(sender=type(order), order=order)

        assert queued.call_count == 1


def _dispute_mail(mailoutbox):
    [mail] = [m for m in mailoutbox if " dispute " in m.subject]
    return mail


class TestStaffEmail:
    @pytest.fixture(autouse=True)
    def _english_store(self, bind_tenant):
        """Staff mail speaks the store's language; this store's is English."""
        bind_tenant(SimpleNamespace(default_locale="en", schema_name="public"))

    def _send(self, order, provider="stripe", **kwargs):
        from order.tasks import send_dispute_notification_email

        with patch(
            "emails.recipients.tenant_operator_addresses",
            return_value=["staff@example.com"],
        ):
            return send_dispute_notification_email.run(
                order.id, DISPUTE, provider, **kwargs
            )

    def test_a_lost_dispute_says_so_and_names_the_chargeback(self, mailoutbox):
        order, _charge = _paid_order()
        with patch(
            "order.signals.handlers.send_dispute_notification_email.apply_async"
        ):
            handle_stripe_dispute_closed(
                sender=None, event=_event("evt_mail_1")
            )

        assert self._send(order, lost=True) is True

        mail = _dispute_mail(mailoutbox)
        assert "dispute lost" in mail.subject
        assert "20.00" in mail.body
        assert "closed against you" in mail.body
        assert "NEXT STEPS" not in mail.body

    def test_the_email_names_the_provider(self, mailoutbox):
        order, _charge = _paid_order()

        assert self._send(order, "viva_wallet") is True

        mail = _dispute_mail(mailoutbox)
        assert "Viva Wallet dispute opened" in mail.subject
        assert "Stripe" not in mail.body

    def test_an_opened_dispute_keeps_its_original_email(self, mailoutbox):
        order, _charge = _paid_order()

        assert self._send(order) is True

        mail = _dispute_mail(mailoutbox)
        assert "dispute opened" in mail.subject
        assert "Chargeback" not in mail.body
        assert "NEXT STEPS" in mail.body


class TestAdminHistory:
    def test_the_order_page_shows_the_chargeback_entry(self, client):
        order, _charge = _paid_order()
        _close(_event("evt_admin_1"))

        response = client.get(
            reverse("admin:order_order_change", args=[order.pk])
        )

        assert response.status_code == 200
        assert "Chargeback" in response.content.decode()


@pytest.fixture
def client(client, django_user_model):
    user = django_user_model.objects.create_superuser(
        username="boss", email="boss@example.com", password="x"
    )
    client.force_login(
        user, backend="tenant.auth_backends.PlatformStaffBackend"
    )
    return client
