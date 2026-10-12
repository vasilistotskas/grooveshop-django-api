"""The refund confirmation email, one per refund.

It used to carry a single boolean per order, so the customer of an order
refunded in parts heard about the first part and never again. The email is
now due while the payment ledger holds more refunds than the emails already
sent cover.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core import mail
from djmoney.money import Money

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory, PaymentTransactionFactory
from order.models import Order
from order.payment_ledger import PaymentLedger
from order.tasks import (
    REFUNDS_EMAILED_KEY,
    _mark_refunds_emailed,
    send_refund_confirmation_email,
)

pytestmark = pytest.mark.django_db


def _order():
    order = OrderFactory(
        num_order_items=0,
        status=OrderStatus.DELIVERED,
        payment_status=PaymentStatus.COMPLETED,
        email="customer@example.com",
    )
    PaymentTransactionFactory(order=order, amount=Money("100.00", "EUR"))
    return order


def _refund(order, amount, reference):
    PaymentLedger.record_refund(
        order,
        charge=order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        ),
        amount=Money(amount, "EUR"),
        reference=reference,
    )


def _refund_emails():
    """The refund confirmations sent so far (creating the order sends its
    own mail too)."""
    return [
        message
        for message in mail.outbox
        if "refund_confirmation" in message.extra_headers.get("List-ID", "")
    ]


def _emailed(order) -> int:
    order.refresh_from_db()
    return order.metadata.get(REFUNDS_EMAILED_KEY, 0)


def test_the_customer_is_told_about_a_refund_once():
    order = _order()
    _refund(order, "30.00", "re_1")

    assert send_refund_confirmation_email(order.id) is True
    # The signal fires again, or the task is retried: nothing new to tell.
    assert send_refund_confirmation_email(order.id) is True

    assert len(_refund_emails()) == 1
    assert _refund_emails()[0].to == ["customer@example.com"]
    assert _emailed(order) == 1


def test_each_further_refund_is_told_too():
    order = _order()
    _refund(order, "30.00", "re_1")
    send_refund_confirmation_email(order.id)

    _refund(order, "25.00", "re_2")
    send_refund_confirmation_email(order.id)
    _refund(order, "45.00", "re_3")
    send_refund_confirmation_email(order.id)

    assert len(_refund_emails()) == 3
    assert _emailed(order) == 3


def test_refunds_made_before_the_email_runs_share_one():
    order = _order()
    _refund(order, "30.00", "re_1")
    _refund(order, "20.00", "re_2")

    send_refund_confirmation_email(order.id)
    send_refund_confirmation_email(order.id)

    assert len(_refund_emails()) == 1
    assert _emailed(order) == 2


def test_an_order_with_no_refund_has_nothing_to_tell():
    order = _order()

    assert send_refund_confirmation_email(order.id) is True

    assert _refund_emails() == []


def test_a_failed_send_leaves_the_refund_to_be_told_later():
    order = _order()
    _refund(order, "30.00", "re_1")

    with (
        patch(
            "order.tasks.EmailMultiAlternatives.send",
            side_effect=OSError("relay down"),
        ),
        # Called directly, a task's retry re-raises the failure.
        pytest.raises(OSError),
    ):
        send_refund_confirmation_email(order.id)

    assert _emailed(order) == 0
    # The next refund's email covers the one that was never delivered.
    _refund(order, "20.00", "re_2")
    send_refund_confirmation_email(order.id)

    assert len(_refund_emails()) == 1
    assert _emailed(order) == 2


def test_the_count_only_grows():
    order = _order()
    _refund(order, "30.00", "re_1")
    _refund(order, "20.00", "re_2")
    send_refund_confirmation_email(order.id)

    _mark_refunds_emailed(order.id, 1)

    assert Order.objects.get(pk=order.pk).metadata[REFUNDS_EMAILED_KEY] == 2
