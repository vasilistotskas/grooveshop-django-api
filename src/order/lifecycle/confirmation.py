"""When an order is confirmed, and the backfill of ``Order.confirmed_at``.

An order is confirmed once (``OrderLifecycleService.confirm``): when it is
placed, unless a card payment is still due online, and otherwise when that
payment settles. Confirming is what tells people about it.
"""

from __future__ import annotations

from datetime import datetime
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


def backfill_confirmed_at(apps: Apps, *, rollout_finished_at: datetime) -> int:
    """Stamp ``confirmed_at = created_at`` on the orders without one that
    the previous release, which had no column, already announced, and say
    how many:

    - an order with nothing to pay online (``due_for_confirmation``'s
      offline kinds) created before *rollout_finished_at* (when the release
      that confirms orders finished rolling out): it was announced as it
      was placed;
    - an online order whose confirmation email went out
      (``CONFIRMATION_EMAIL_SENT_AT_KEY`` in its ``metadata``). The new
      release sends that email only from a committed confirmation, so an
      unconfirmed order that carries it was announced by the previous one.

    Any other online order, paid during the rollout or since, is the new
    release's to confirm (on its payment, or on the next sight of it), and
    stamping it would silence its announcement. Idempotent; it tells no
    one. An announcement the previous release still had queued is stamped
    by a later run.
    """
    online = Q(pay_way__settlement=PaySettlement.ONLINE)
    offline = Q(pay_way__isnull=True) | ~online
    return (
        apps.get_model("order", "Order")
        .objects.filter(confirmed_at__isnull=True)
        .filter(
            (offline & Q(created_at__lt=rollout_finished_at))
            | (online & Q(metadata__has_key=CONFIRMATION_EMAIL_SENT_AT_KEY))
        )
        .update(confirmed_at=F("created_at"))
    )
