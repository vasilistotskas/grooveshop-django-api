"""When an order is confirmed, and the backfill of ``Order.confirmed_at``.

An order is confirmed once (``OrderLifecycleService.confirm``): when it is
placed, unless a card payment is still due online, and otherwise when that
payment settles. Confirming is what tells people about it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import F, Q

from order.enum.status import CHARGED_PAYMENT_STATUSES, PaymentStatus
from pay_way.enum.settlement import PaySettlement

#: The ``Order.metadata`` key ``send_order_confirmation_email`` writes once
#: the confirmation email went out: the permanent guard against sending it
#: twice, and the backfill's evidence of an announcement.
CONFIRMATION_EMAIL_SENT_AT_KEY = "confirmation_email_sent_at"

if TYPE_CHECKING:
    from django.apps.registry import Apps

    from order.models.order import Order


def due_for_confirmation() -> Q:
    """The orders due to be confirmed (``OrderLifecycleService.confirm``):
    nothing is left to pay online, or the online payment is COMPLETED. Cash
    or card on delivery, BOX NOW pay on the go and bank transfer are paid
    later, offline, so they are due as they are placed; a card order is due
    once its payment went through. An order whose pay way was deleted is
    treated as offline, so its customer still hears about it."""
    return (
        Q(pay_way__isnull=True)
        | ~Q(pay_way__settlement=PaySettlement.ONLINE)
        | Q(payment_status=PaymentStatus.COMPLETED)
    )


def paid_online(order: Order) -> bool:
    """Whether *order* was confirmed by an online payment that went
    through, which its in-app note says rather than "order placed"."""
    pay_way = order.pay_way
    return (
        pay_way is not None
        and PaySettlement(pay_way.settlement) == PaySettlement.ONLINE
        and order.payment_status in CHARGED_PAYMENT_STATUSES
    )


def backfill_confirmed_at(apps: Apps) -> int:
    """Stamp ``confirmed_at = created_at`` on the orders the previous
    release, which had no column, already announced, and say how many: the
    unconfirmed orders whose confirmation email went out
    (``CONFIRMATION_EMAIL_SENT_AT_KEY`` in their ``metadata``), offline and
    online alike. The new release sends that email only from a committed
    confirmation, so an unconfirmed order carrying the key was announced by
    the previous one, and an order without it, whenever it was placed or
    paid, is the new release's to confirm: stamping it would silence its
    announcement. An order the previous release announced while the store
    had the confirmation email switched off carries no key and is left
    unconfirmed. Idempotent; it tells no one. An announcement the previous
    release still had queued is stamped by a later run.
    """
    return (
        apps.get_model("order", "Order")
        .objects.filter(
            confirmed_at__isnull=True,
            metadata__has_key=CONFIRMATION_EMAIL_SENT_AT_KEY,
        )
        .update(confirmed_at=F("created_at"))
    )
