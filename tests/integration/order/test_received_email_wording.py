"""What the order-received email says, per way of paying.

The webside owner reviewed a cash-on-delivery confirmation (order 413,
2026-10-16) and asked for:

- the title «Λάβαμε την Παραγγελία σας!», keeping the order number in
  the subject so each order stays its own thread;
- the payment-status row only when the order is paid;
- no "how to complete your payment" box for cash the courier collects:
  it told the shopper to put the order number in a payment reference
  they never write. The box stays where the shopper does have to pay:
  a bank transfer (with the reference line) and BOX NOW's pay-on-the-go,
  paid through the carrier's link (without it);
- a line saying the shopper will hear about the order's progress.

Runs the real task and templates in Greek; only the mail transport is
replaced.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.db.models.signals import post_save
from factory.django import mute_signals

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.tasks import send_order_confirmation_email
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory

pytestmark = pytest.mark.django_db

INSTRUCTIONS = "Πληρώστε στον λογαριασμό GR00 0000 0000 0000."


def _send(settlement: PaySettlement) -> tuple[str, str, str]:
    """Send the confirmation for an unpaid order; return the subject and
    the text and HTML bodies."""
    pay_way = PayWayFactory(settlement=settlement, active=True)
    for language in ("el", "en", "de"):
        pay_way.set_current_language(language)
        pay_way.instructions = INSTRUCTIONS
    pay_way.save()
    with mute_signals(post_save):
        order = OrderFactory.create(
            num_order_items=1,
            pay_way=pay_way,
            language_code="el",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )

    with patch("order.tasks.EmailMultiAlternatives") as mail:
        message = MagicMock()
        mail.return_value = message
        assert send_order_confirmation_email(order.id)

    subject, text = mail.call_args.args[0], mail.call_args.args[1]
    html = message.attach_alternative.call_args.args[0]
    assert subject == f"Λάβαμε την Παραγγελία σας! — #{order.id}"
    return subject, text, html


def _no_payment_due_now(text: str, html: str) -> None:
    assert "Λάβαμε την Παραγγελία σας!" in html
    assert "ΛΑΒΑΜΕ ΤΗΝ ΠΑΡΑΓΓΕΛΙΑ ΣΑΣ!" in text
    for body in (text, html):
        assert "Κατάσταση πληρωμής" not in body
        assert "αιτιολογία της πληρωμής" not in body
        assert "αναμένουμε την πληρωμή σας" not in body
        assert "Σε αναμονή πληρωμής" not in body
        assert "Ευχαριστούμε — λάβαμε την παραγγελία σας." in body
        assert (
            "Θα ενημερωθείτε με νέο μήνυμα σχετικά με την πορεία της "
            "παραγγελίας σας." in body
        )


def test_cash_to_the_courier_gets_no_payment_instructions():
    _subject, text, html = _send(PaySettlement.COURIER_CASH)

    _no_payment_due_now(text, html)
    for body in (text, html):
        assert INSTRUCTIONS not in body
    assert "Πώς να ολοκληρώσετε την πληρωμή σας" not in html


def test_pay_at_the_locker_keeps_its_instructions_without_a_reference():
    """BOX NOW pay-on-the-go is paid through the carrier's link before the
    locker opens, so the store's instructions still help; there is no
    payment reference to write."""
    _subject, text, html = _send(PaySettlement.CARRIER_TERMINAL)

    _no_payment_due_now(text, html)
    assert "Πώς να ολοκληρώσετε την πληρωμή σας" in html
    for body in (text, html):
        assert INSTRUCTIONS in body


def test_a_bank_transfer_keeps_its_instructions_and_reference():
    _subject, text, html = _send(PaySettlement.OFFLINE_TRANSFER)

    assert "Πώς να ολοκληρώσετε την πληρωμή σας" in html
    assert "ΠΩΣ ΝΑ ΟΛΟΚΛΗΡΩΣΕΤΕ ΤΗΝ ΠΛΗΡΩΜΗ ΣΑΣ" in text
    for body in (text, html):
        assert INSTRUCTIONS in body
        assert "στην αιτιολογία της πληρωμής." in body
        assert "αναμένουμε την πληρωμή σας" in body
        # Unpaid, so no payment-status row.
        assert "Κατάσταση πληρωμής" not in body
