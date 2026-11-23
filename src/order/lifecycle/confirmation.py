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

if TYPE_CHECKING:
    from django.apps.registry import Apps

    from order.models.order import Order


def confirmed_at_creation(order: Order) -> bool:
    """Whether *order* is confirmed as it is placed: nothing is left to pay
    online. Cash or card on delivery, BOX NOW pay on the go and bank
    transfer are paid later, offline; an online order already paid has
    nothing left to wait for. An order whose pay way was deleted is treated
    as offline, so its customer still hears about it."""
    pay_way = order.pay_way
    return (
        pay_way is None
        or PaySettlement(pay_way.settlement) != PaySettlement.ONLINE
        or order.payment_status == PaymentStatus.COMPLETED
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
    """Stamp ``confirmed_at = created_at`` on every order without one that
    is not an online order never paid, and say how many. Those were
    confirmed under the release that had no column: an offline order at
    creation, an online one by its payment. Idempotent; it tells no one.
    """
    never_paid_online = Q(pay_way__settlement=PaySettlement.ONLINE) & ~Q(
        payment_status__in=CHARGED_PAYMENT_STATUSES
    )
    return (
        apps.get_model("order", "Order")
        .objects.filter(confirmed_at__isnull=True)
        .exclude(never_paid_online)
        .update(confirmed_at=F("created_at"))
    )
