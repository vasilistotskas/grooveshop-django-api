"""The refund confirmation email: one per REFUND ledger row, claimed
atomically before it is sent.

It used to compare a count of refunds with a count of emails sent and mark
the whole count after one send, so two workers could both send, and one
task could swallow another refund's email. The claim is now the insert of a
``RefundNotification`` row, unique per refund.
"""

from __future__ import annotations

import re
import threading
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core import mail
from django.db import IntegrityError, connection, transaction
from djmoney.money import Money

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory, PaymentTransactionFactory
from order.models import OrderHistory, PaymentTransaction, RefundNotification
from order.payment_ledger import PaymentLedger
from order.services import OrderService
from order.tasks import _claim_refund_email, send_refund_confirmation_email
from pay_way.factories import PayWayFactory
from tests.utils.stripe_refunds import FakeStripeRefunds

pytestmark = pytest.mark.django_db


def _order(**kwargs):
    order = OrderFactory(
        num_order_items=0,
        status=OrderStatus.DELIVERED,
        payment_status=PaymentStatus.COMPLETED,
        email="customer@example.com",
        **kwargs,
    )
    PaymentTransactionFactory(order=order, amount=Money("100.00", "EUR"))
    return order


def _refund(order, amount, reference) -> PaymentTransaction:
    row, _created = PaymentLedger.record_refund(
        order,
        charge=order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        ),
        amount=Money(amount, "EUR"),
        reference=reference,
    )
    return row


def _refund_emails():
    """The refund confirmations sent so far (creating the order sends its
    own mail too)."""
    return [
        message
        for message in mail.outbox
        if "refund_confirmation" in message.extra_headers.get("List-ID", "")
    ]


def _claimed(row) -> bool:
    return RefundNotification.objects.filter(refund=row).exists()


def test_the_customer_is_told_about_a_refund_once():
    order = _order()
    refund = _refund(order, "30.00", "re_1")

    assert send_refund_confirmation_email(refund.pk) is True
    # The task is delivered again, or the signal fires twice: nothing new.
    assert send_refund_confirmation_email(refund.pk) is True

    assert len(_refund_emails()) == 1
    assert _refund_emails()[0].to == ["customer@example.com"]
    assert _claimed(refund)


def test_two_refunds_recorded_before_their_tasks_run_each_get_an_email():
    """The shape the old count could not tell apart: both refunds exist
    when the first task runs, and the first email must not stand in for the
    second."""
    order = _order()
    first = _refund(order, "30.00", "re_1")
    second = _refund(order, "20.00", "re_2")

    send_refund_confirmation_email(first.pk)
    send_refund_confirmation_email(second.pk)

    assert len(_refund_emails()) == 2
    assert _claimed(first)
    assert _claimed(second)


def test_a_duplicated_task_sends_once_per_refund():
    order = _order()
    first = _refund(order, "30.00", "re_1")
    second = _refund(order, "20.00", "re_2")

    for refund in (first, first, second, second, first):
        send_refund_confirmation_email(refund.pk)

    assert len(_refund_emails()) == 2


def test_a_refund_is_told_about_even_after_the_ones_before_it_were():
    order = _order()
    first = _refund(order, "30.00", "re_1")
    send_refund_confirmation_email(first.pk)

    second = _refund(order, "25.00", "re_2")
    third = _refund(order, "45.00", "re_3")
    send_refund_confirmation_email(second.pk)
    send_refund_confirmation_email(third.pk)

    assert len(_refund_emails()) == 3


def test_only_a_refund_row_can_be_told_about():
    order = _order()
    charge = order.payment_transactions.get(kind=PaymentTransactionKind.CHARGE)

    assert send_refund_confirmation_email(charge.pk) is False
    assert send_refund_confirmation_email(0) is False

    assert _refund_emails() == []
    assert not RefundNotification.objects.exists()


def test_the_history_says_which_refund_the_email_was_for():
    order = _order()
    refund = _refund(order, "30.00", "re_1")

    send_refund_confirmation_email(refund.pk)

    notes = [
        row.new_value["note"]
        for row in OrderHistory.objects.filter(order=order, change_type="NOTE")
    ]
    assert (
        f"Refund confirmation email sent to customer@example.com "
        f"({refund.amount})"
    ) in notes


class TestAFailedSend:
    def test_the_claim_is_held_while_a_retry_is_pending(self):
        """The first attempt failed and a retry is queued: a duplicate task
        meanwhile must not send, or the customer gets two emails once the
        retry succeeds."""
        order = _order()
        refund = _refund(order, "30.00", "re_1")

        with (
            patch(
                "order.tasks.EmailMultiAlternatives.send",
                side_effect=OSError("relay down"),
            ),
            # Called directly, a task's retry re-raises the failure.
            pytest.raises(OSError),
        ):
            send_refund_confirmation_email(refund.pk)
        assert _claimed(refund)

        assert send_refund_confirmation_email(refund.pk) is True
        assert _refund_emails() == []

    def test_the_claim_is_given_back_when_the_send_fails_for_good(self):
        order = _order()
        refund = _refund(order, "30.00", "re_1")

        with (
            patch(
                "order.tasks.EmailMultiAlternatives.send",
                side_effect=OSError("relay down"),
            ),
            patch.object(send_refund_confirmation_email, "max_retries", 0),
        ):
            assert send_refund_confirmation_email(refund.pk) is False

        # Not recorded as told: a later dispatch can send it.
        assert not _claimed(refund)
        assert send_refund_confirmation_email(refund.pk) is True
        assert len(_refund_emails()) == 1

    def test_a_send_that_went_out_is_never_repeated(self):
        """Whatever fails after the relay took the message (here the
        history note) must not retry into a second email."""
        order = _order()
        refund = _refund(order, "30.00", "re_1")

        with patch(
            "order.tasks.OrderHistory.log_note", side_effect=RuntimeError("db")
        ):
            assert send_refund_confirmation_email(refund.pk) is True

        assert len(_refund_emails()) == 1
        assert _claimed(refund)


class TestTheClaim:
    def test_the_first_caller_wins_and_the_next_does_not(self):
        refund = _refund(_order(), "30.00", "re_1")

        assert _claim_refund_email(refund) is True
        assert _claim_refund_email(refund) is False

    def test_a_claim_is_per_refund(self):
        order = _order()
        first = _refund(order, "30.00", "re_1")
        second = _refund(order, "20.00", "re_2")

        assert _claim_refund_email(first) is True
        assert _claim_refund_email(second) is True

    def test_the_database_refuses_a_second_claim_row(self):
        """What makes the claim atomic: not the check before the insert,
        the unique key on it."""
        refund = _refund(_order(), "30.00", "re_1")
        RefundNotification.objects.create(refund=refund)

        with pytest.raises(IntegrityError), transaction.atomic():
            RefundNotification.objects.create(refund=refund)

    def test_the_claim_goes_with_the_ledger_row(self):
        refund = _refund(_order(), "30.00", "re_1")
        _claim_refund_email(refund)

        PaymentTransaction.objects.filter(pk=refund.pk).delete()

        assert not RefundNotification.objects.exists()


class TestWorkersRacingForOneRefund:
    """Real commits on separate connections: the point of the claim."""

    @pytest.mark.django_db(transaction=True)
    def test_only_one_of_several_workers_sends(self):
        order = _order()
        refund = _refund(order, "30.00", "re_1")
        workers = 4
        barrier = threading.Barrier(workers)
        outcomes: list[bool | Exception] = []

        def work():
            connection.close()
            barrier.wait()
            try:
                outcomes.append(send_refund_confirmation_email(refund.pk))
            except Exception as exc:  # reported below, not swallowed
                outcomes.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=work) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert outcomes == [True] * workers
        assert len(_refund_emails()) == 1
        assert RefundNotification.objects.filter(refund=refund).count() == 1


class TestEachRefundQueuesItsOwnTask:
    def test_two_in_app_refunds_queue_two_tasks_on_commit(
        self, django_capture_on_commit_callbacks
    ):
        order = OrderFactory(
            num_order_items=0,
            pay_way=PayWayFactory.create_online_payment(),
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.COMPLETED,
            payment_id="pi_ledger",
            amount_due=Money(Decimal("100.00"), "EUR"),
        )
        PaymentTransactionFactory(
            order=order,
            amount=Money("100.00", "EUR"),
            provider_reference="pi_ledger",
        )

        with (
            patch(
                "order.signals.handlers.send_refund_confirmation_email"
                ".apply_async"
            ) as queued,
            FakeStripeRefunds(),
            django_capture_on_commit_callbacks(execute=True),
        ):
            OrderService.refund_order(order, amount=Money("30.00", "EUR"))
            OrderService.refund_order(order, amount=Money("20.00", "EUR"))

        rows = order.payment_transactions.filter(
            kind=PaymentTransactionKind.REFUND
        )
        assert [call.kwargs["args"] for call in queued.call_args_list] == [
            [row.pk] for row in rows
        ]
        assert queued.call_count == 2


class TestTheEmailStatesTheAmount:
    """A customer refunded 30 of 100 must be told 30 and that 70 is still
    to come, not just that "your refund has been processed"."""

    def _bodies(self, message) -> tuple[str, str]:
        html = message.alternatives[0].content
        return message.body, html

    def test_a_partial_refund_says_how_much_and_how_much_is_left(self):
        order = _order(language_code="en")
        refund = _refund(order, "30.00", "re_1")

        send_refund_confirmation_email(refund.pk)

        [message] = _refund_emails()
        for body in self._bodies(message):
            assert "Amount refunded" in body
            assert re.search(r"30[.,]00", body)
            assert "Remaining payment on this order" in body
            assert re.search(r"70[.,]00", body)

    def test_a_full_refund_says_how_much_and_nothing_is_left(self):
        order = _order(language_code="en")
        refund = _refund(order, "100.00", "re_1")

        send_refund_confirmation_email(refund.pk)

        [message] = _refund_emails()
        for body in self._bodies(message):
            assert "Amount refunded" in body
            assert re.search(r"100[.,]00", body)
            assert "Remaining payment on this order" not in body

    def test_each_refund_email_names_its_own_amount(self):
        order = _order(language_code="en")
        first = _refund(order, "30.00", "re_1")
        second = _refund(order, "20.00", "re_2")

        send_refund_confirmation_email(first.pk)
        send_refund_confirmation_email(second.pk)

        one, two = _refund_emails()
        assert re.search(r"30[.,]00", one.body)
        assert re.search(r"20[.,]00", two.body)
        assert re.search(r"50[.,]00", two.body)

    def test_the_status_update_email_has_no_refund_block(self):
        """The generic status email (a REFUNDED status) carries no refund
        row of its own, so it must not print an empty amount."""
        from django.template.loader import render_to_string

        order = _order(language_code="en")
        body = render_to_string(
            "emails/order/order_status_generic.txt",
            {"order": order, "status": "REFUNDED", "status_display": "R"},
        )

        assert "Amount refunded" not in body
