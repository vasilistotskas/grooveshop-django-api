"""When an order is confirmed, and the backfill of ``Order.confirmed_at``.

An order is confirmed once (``OrderLifecycleService.confirm``): when it is
placed, unless a card payment is still due online, and otherwise when that
payment settles. Confirming is what tells people about it.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from django.db.models import Exists, F, OuterRef, Q

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import CHARGED_PAYMENT_STATUSES, PaymentStatus
from pay_way.enum.settlement import PaySettlement

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
    the previous release, which had no column, announced or settled before
    *rollout_finished_at* (when the release that confirms orders finished
    rolling out), and say how many:

    - an order with nothing to pay online (``due_for_confirmation``'s
      offline kinds) created before then: it was announced as it was
      placed;
    - an online order paid (``CHARGED_PAYMENT_STATUSES``) whose payment
      the ledger booked before then: a CHARGE ``PaymentTransaction``
      with ``created_at`` before *rollout_finished_at*. The order has no
      paid-at of its own, and that row is the moment its payment was
      recorded by a webhook, a checkout session or a read-back.

    An online order whose payment is recorded since, whenever it was
    placed, is the new release's to confirm (on its payment, or on the next
    sight of it), and stamping it would silence its announcement.
    Idempotent; it tells no one.
    """
    offline = Q(pay_way__isnull=True) | ~Q(
        pay_way__settlement=PaySettlement.ONLINE
    )
    booked_before = Exists(
        apps.get_model("order", "PaymentTransaction").objects.filter(
            order=OuterRef("pk"),
            kind=PaymentTransactionKind.CHARGE,
            created_at__lt=rollout_finished_at,
        )
    )
    return (
        apps.get_model("order", "Order")
        .objects.filter(confirmed_at__isnull=True)
        .filter(
            (offline & Q(created_at__lt=rollout_finished_at))
            | (
                Q(pay_way__settlement=PaySettlement.ONLINE)
                & Q(payment_status__in=CHARGED_PAYMENT_STATUSES)
                & booked_before
            )
        )
        .update(confirmed_at=F("created_at"))
    )
