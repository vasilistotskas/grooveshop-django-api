"""Give orders that were never snapshotted their discount allocation.

``OrderItem.discount_allocation`` (``order`` 0072) is written at checkout.
Two kinds of order do not have it: those that predate the column, and
those the previous release creates while it still serves the rollout (its
INSERTs do not name the column, so every line holds the ``db_default`` 0).
The tax documents refuse such an order (``order.discounts``) rather than
bill a total the customer never owed, so this fills them in.

The only information that exists for them is the order's discount total:
a ``PromotionRedemption`` row records the promotion and its amount, not
the lines it was earned on, and the promotion may have been edited since.
So the discount is spread proportionally over the lines by value, the rule
every one of their documents was issued under. That keeps the figures of an
order that already has an invoice what they were; ``Invoice`` rows are
never touched here, and one registered with myDATA is frozen anyway
(``order.invoicing``).

It takes the migration registry rather than the models so the one function
serves both the data migration (the historical models of its point in the
graph, which later columns cannot break) and the management command (the
live ones). It reads raw column values, never model methods, for the same
reason. An order is "never snapshotted" when none of its lines carries an
allocation, so a second run changes nothing.
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Exists, OuterRef, Q

from order.discounts import CENT, allocate_capped


def heal_discount_allocation(apps) -> int:
    """Snapshot every discounted order whose lines carry none.

    Returns how many orders were written.
    """
    Order = apps.get_model("order", "Order")
    OrderItem = apps.get_model("order", "OrderItem")

    snapshotted = OrderItem._base_manager.filter(
        order=OuterRef("pk"), discount_allocation__gt=0
    )
    discounted = (
        Order._base_manager.filter(
            Q(discount_amount__gt=0) | Q(loyalty_discount__gt=0)
        )
        .annotate(has_snapshot=Exists(snapshotted))
        .filter(has_snapshot=False)
        .values_list("pk", "discount_amount", "loyalty_discount")
    )

    healed = 0
    for order_id, promotion, loyalty in discounted.iterator():
        lines = {
            pk: Decimal(price) * quantity
            for pk, price, quantity in OrderItem._base_manager.filter(
                order_id=order_id
            ).values_list("pk", "price", "quantity")
        }
        discount = (
            sum(
                max(Decimal(0), Decimal(value))
                for value in (promotion, loyalty)
            )
        ).quantize(CENT)
        shares = allocate_capped(discount, lines, lines)
        written = {pk: share for pk, share in shares.items() if share > 0}
        for pk, share in written.items():
            OrderItem._base_manager.filter(pk=pk).update(
                discount_allocation=share
            )
        healed += bool(written)
    return healed
