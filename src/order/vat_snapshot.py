"""Snapshot what the turnover report reads: VAT lines and the ship moment.

``OrderVatLine`` rows are written when an order is created
(``write_vat_lines``, from both creation paths of ``OrderService``) by the
very function the receipt and the myDATA payload use
(``order.taxation.order_vat_lines``), so no figure can differ from the
receipt. ``Order.shipped_at`` is stamped by ``Order.save`` the first time
the order is in a dispatched status.

The backfills take the migration registry rather than the models, like
``order.discount_snapshot``: the one function serves the data migration
(the historical models of its point in the graph) and the management
command (the live ones), and both run the same live ``order_vat_lines`` on
objects that carry the same attributes. Both are idempotent. Orders the
previous release creates or ships while it still serves a rollout are not
covered by the migration; ``manage.py backfill_turnover_snapshots`` after
the rollout covers them.
"""

from __future__ import annotations

import logging

from django.db.models import Exists, F, OuterRef, Subquery
from django.db.models.functions import Coalesce

from order.enum.status import DISPATCHED_STATUSES
from order.taxation import VatLine, order_vat_lines

logger = logging.getLogger(__name__)


def _rows(model, order_pk: int, lines: list[VatLine]) -> list:
    return [
        model(
            order_id=order_pk,
            item_id=line.item_pk,
            kind=line.kind,
            vat_rate=line.rate,
            net=line.net,
            vat=line.vat,
        )
        for line in lines
    ]


def write_vat_lines(order) -> None:
    """Replace ``order``'s VAT lines with what its receipt bills now.

    Called once, at the end of order creation, when the items, the
    discounts and the charges are all final.
    """
    from order.models import OrderVatLine

    OrderVatLine.objects.filter(order=order).delete()
    OrderVatLine.objects.bulk_create(
        _rows(OrderVatLine, order.pk, order_vat_lines(order))
    )


def backfill_vat_lines(apps) -> tuple[int, list[int]]:
    """Write the lines of every order that has none.

    Returns ``(orders written, orders skipped)``. An order whose receipt
    cannot be derived (``order_vat_lines`` raises: a discount that does
    not add up over its lines, a charge with no goods to follow) is
    skipped and named, never guessed. An order that bills nothing has no
    lines and is looked at again on each run, which costs one read.
    """
    Order = apps.get_model("order", "Order")
    OrderVatLine = apps.get_model("order", "OrderVatLine")

    lined = OrderVatLine._base_manager.filter(order=OuterRef("pk"))
    pending = (
        Order._base_manager.annotate(has_lines=Exists(lined))
        .filter(has_lines=False)
        .prefetch_related("items")
    )

    written = 0
    skipped: list[int] = []
    for order in pending.iterator(chunk_size=500):
        try:
            lines = order_vat_lines(order)
        except ValueError:
            logger.warning("Order %s: no VAT lines derivable", order.pk)
            skipped.append(order.pk)
            continue
        if lines:
            OrderVatLine._base_manager.bulk_create(
                _rows(OrderVatLine, order.pk, lines)
            )
            written += 1
    return written, skipped


def backfill_shipped_at(apps) -> int:
    """Stamp ``shipped_at`` on every dispatched order that has none.

    The moment is the first status change into a dispatched status in the
    order's history; an order with no such row (seeded data written around
    ``Order.save``) takes its last status change, then its creation: the
    nearest real timestamp the order holds. Returns how many were stamped.
    """
    Order = apps.get_model("order", "Order")
    OrderHistory = apps.get_model("order", "OrderHistory")

    first_dispatch = (
        OrderHistory._base_manager.filter(
            order=OuterRef("pk"),
            change_type="STATUS",
            new_value__status__in=sorted(DISPATCHED_STATUSES),
        )
        .order_by("created_at")
        .values("created_at")[:1]
    )
    return Order._base_manager.filter(
        shipped_at__isnull=True, status__in=sorted(DISPATCHED_STATUSES)
    ).update(
        shipped_at=Coalesce(
            Subquery(first_dispatch),
            F("status_updated_at"),
            F("created_at"),
        )
    )
