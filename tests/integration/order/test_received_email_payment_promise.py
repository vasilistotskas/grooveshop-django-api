"""The order-received email only promises a second email when one is
actually coming.

The line read "You will receive a second email as soon as we confirm
your payment" on EVERY order. For cash on delivery that is a promise
nobody keeps: the carrier takes the money at the door, the order sits
PENDING until the courier pays out days later, and no confirmation
email is ever sent. The store owner spotted it on his own COD test
order (#281, 2026-09-12).

It stays for the payments we really do confirm ourselves — an online
payment that has not completed, and a bank transfer we mark as
received.
"""

from __future__ import annotations

import pytest
from django.template.loader import render_to_string

from order.factories.order import OrderFactory
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory

pytestmark = pytest.mark.django_db

TEMPLATES = (
    "emails/order/order_received.html",
    "emails/order/order_received.txt",
)

# The templates render under the store's own locale, so the needle has
# to match either language — an English-only needle made every
# "promises nothing" assertion pass for the wrong reason.
PROMISES = ("second email", "δεύτερο email")


def _promises(rendered: str) -> bool:
    return any(needle in rendered for needle in PROMISES)


def _rendered(*, settlement, is_paid=False) -> str:
    """Render both parts with the flag the task computes for this
    settlement, and return them joined — the promise must be absent
    from the plain-text part too, not just the HTML."""
    pay_way = PayWayFactory(settlement=settlement, active=True)
    order = OrderFactory(num_order_items=1, pay_way=pay_way)
    expects = bool(
        pay_way and not is_paid and not pay_way.is_collected_on_delivery
    )
    context = {
        "order": order,
        "items": order.items.all(),
        "pay_way": pay_way,
        "is_paid": is_paid,
        "expects_payment_confirmation": expects,
        "SITE_NAME": "Webside",
        "payment_instructions": "",
        "payment_instructions_text": "",
    }
    return "\n".join(render_to_string(name, context) for name in TEMPLATES)


def test_cash_on_delivery_promises_nothing():
    assert not _promises(_rendered(settlement=PaySettlement.COURIER_CASH))


def test_paying_at_the_locker_promises_nothing():
    """Same reasoning: the carrier collects, we never confirm."""
    assert not _promises(_rendered(settlement=PaySettlement.CARRIER_TERMINAL))


def test_an_unpaid_online_order_still_promises_the_confirmation():
    assert _promises(_rendered(settlement=PaySettlement.ONLINE, is_paid=False))


def test_a_bank_transfer_still_promises_the_confirmation():
    """We mark the transfer received ourselves, so a second email does
    follow."""
    assert _promises(_rendered(settlement=PaySettlement.OFFLINE_TRANSFER))


def test_an_already_paid_order_promises_nothing_further():
    assert not _promises(
        _rendered(settlement=PaySettlement.ONLINE, is_paid=True)
    )
