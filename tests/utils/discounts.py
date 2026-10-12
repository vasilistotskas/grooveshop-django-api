"""Write an order's discount allocation the way checkout does.

An order built by hand with ``discount_amount`` / ``loyalty_discount`` has
no snapshot on its lines, and the tax documents refuse it
(``order.discounts.discounted_line_gross``). ``snapshot_discounts`` gives
its lines what ``OrderService`` would have written.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from order.discounts import (
    add_line_discounts,
    spread_by_remaining_value,
)


def snapshot_discounts(
    order, promotion_lines: Mapping[int, Decimal] | None = None
) -> None:
    """Snapshot ``order``'s discounts onto its lines.

    ``promotion_lines`` is ``OrderItem.pk`` -> the promotion money that
    line carries, as the engine reports it for a scoped promotion. Without
    it the promotion discount is order-wide and is spread over every line by
    value, like the loyalty discount always is.
    """
    items = list(order.items.all())
    if promotion_lines is None:
        promotion_lines = spread_by_remaining_value(
            items, Decimal(order.discount_amount.amount)
        )
    add_line_discounts(items, promotion_lines)
    add_line_discounts(
        items,
        spread_by_remaining_value(
            items, Decimal(order.loyalty_discount.amount)
        ),
    )
