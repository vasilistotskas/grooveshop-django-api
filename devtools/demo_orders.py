"""Fixture orders for the demo store: history, never a sale.

The demo shopper accounts and the reviewer accounts both need orders —
an order list for a prospect to read, and COMPLETED orders that make a
review a verified purchase. Both are written here, so the rule that
keeps them silent lives in one place.

A seeded order must not send a confirmation email, must not move stock
and must not enter the state machine. ``Order.save()`` does all three
through ``post_save`` (``handle_order_post_save`` fires ``order_created``,
which mails the shopper and the merchant, pushes a WebSocket toast and an
agent-gateway event), and so does ``OrderItem.save()``
(``handle_order_item_post_save``). ``bulk_create`` sends no ``post_save``
(Django's documented behaviour), so the rows go in that way and are
backdated afterwards with ``bulk_update``, which writes the ``auto_now``
columns as given.

``bulk_create`` also skips the two things ``Order.save()`` derives, and
both are written here: ``pay_way_key`` through the model's own snapshot,
and ``paid_amount`` — what the shopper owed, as on a real order.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from djmoney.money import Money

CENT = Decimal("0.01")


@dataclass(frozen=True)
class FixtureLine:
    product: Any
    quantity: int
    #: The unit price as the order snapshotted it (VAT-inclusive).
    price: Money


@dataclass
class FixtureOrder:
    #: Column values for ``Order`` (``user``, address, ``status`` …).
    #: Money columns are ``Money``, never a bare number.
    fields: dict[str, Any]
    lines: Sequence[FixtureLine]
    placed_at: datetime
    #: When the order last changed state; defaults to ``placed_at``.
    updated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def owed(fields: dict[str, Any], lines: Sequence[FixtureLine]) -> Money:
    """What the shopper owes: lines and extras, less every deduction.

    The same sum as ``Order.calculate_order_total_amount``, computed
    from the fixture because the order has no lines to total until
    they are written.
    """
    total = sum(
        (line.price.amount * line.quantity for line in lines), Decimal(0)
    )
    for extra in ("shipping_price", "payment_method_fee"):
        value = fields.get(extra)
        if value is not None:
            total += value.amount
    for deduction in (
        "discount_amount",
        "loyalty_discount",
        "gift_card_amount",
    ):
        value = fields.get(deduction)
        if value is not None and value.amount > 0:
            total -= value.amount
    return Money(max(Decimal(0), total).quantize(CENT), "EUR")


def create_orders(fixtures: Sequence[FixtureOrder]) -> list[Any]:
    """Write the orders and their lines silently, backdated.

    Returns the orders in the order given, each with its primary key.
    """
    from order.models.item import OrderItem
    from order.models.order import Order

    if not fixtures:
        return []

    orders = []
    for fixture in fixtures:
        order = Order(**fixture.fields, metadata=fixture.metadata)
        order._snapshot_pay_way_key()
        orders.append(order)
    created = Order.objects.bulk_create(orders)

    items = []
    for order, fixture in zip(created, fixtures, strict=True):
        for line in fixture.lines:
            items.append(
                OrderItem(
                    order=order,
                    product=line.product,
                    quantity=line.quantity,
                    original_quantity=line.quantity,
                    price=line.price,
                )
            )
        updated_at = fixture.updated_at or fixture.placed_at
        order.paid_amount = owed(fixture.fields, fixture.lines)
        order.created_at = fixture.placed_at
        order.updated_at = updated_at
        order.status_updated_at = updated_at
    written = OrderItem.objects.bulk_create(items)
    Order.objects.bulk_update(
        created,
        ["paid_amount", "created_at", "updated_at", "status_updated_at"],
    )

    placed = {order.pk: order.created_at for order in created}
    for item in written:
        item.created_at = item.updated_at = placed[item.order_id]
    OrderItem.objects.bulk_update(written, ["created_at", "updated_at"])
    return created
