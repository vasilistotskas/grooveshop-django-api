"""Clear a paid online order's lines from its cart inside settlement (#839).

An online order keeps its cart until its payment settles so a shopper who
presses Back still has a basket. Clearing the cart in a post-commit callback
left a gap between the settlement's commit and the cart cleanup in which the
order was no longer unpaid while the cart still held its lines: a checkout
landing in that gap created a second payable order.

Every online settlement path therefore locks the order's cart FIRST
(:func:`lock_cart_for_order_id` or :func:`lock_cart_for_order_fields`),
then locks the order row, and calls :func:`clear_locked_cart_for_order`
inside the same transaction when it transitions the order to paid. Because
checkout (``OrderService._create_order_locked``) and stock reservation
(``StockManager.reserve_cart_stock``) also lock the cart row before any
order or cart-item row, settlement and checkout serialize without
deadlocking, and the paid order and its cleared cart commit atomically.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.db import transaction

if TYPE_CHECKING:
    from cart.models import Cart
    from order.models.order import Order

logger = logging.getLogger(__name__)


class CartUnsetType:
    """Sentinel type for callers that did not pre-lock the order's cart."""


#: Sentinel default on settlement helpers that accept a pre-locked cart.
#: Production entry points lock the cart before the order and pass the
#: locked ``Cart`` (or ``None`` when no cart existed); direct unit-test
#: calls that omit ``cart`` fall back to :func:`clear_cart_for_order`.
CART_UNSET = CartUnsetType()


def _cart_uuid(metadata: Mapping[str, Any] | None) -> UUID | None:
    if not metadata:
        return None
    snapshot = metadata.get("cart_snapshot")
    if not isinstance(snapshot, Mapping):
        return None
    raw = snapshot.get("cart_uuid")
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None


def _cart_queryset(*, user_id: int | None, metadata: Mapping[str, Any] | None):
    from cart.models import Cart

    if user_id is not None:
        return Cart.objects.filter(user_id=user_id)

    cart_uuid = _cart_uuid(metadata)
    if cart_uuid is None:
        return None
    return Cart.objects.filter(uuid=cart_uuid, user__isnull=True)


def lock_cart_for_order_fields(
    *, user_id: int | None, metadata: Mapping[str, Any] | None
) -> Cart | None:
    """Lock the cart an order was built from, or return ``None`` if gone.

    Must run inside ``transaction.atomic()`` BEFORE locking the ``Order``
    row so settlement takes locks in the same order as checkout
    (cart, then order).
    """
    qs = _cart_queryset(user_id=user_id, metadata=metadata)
    if qs is None:
        return None
    return qs.select_for_update().first()


def lock_cart_for_order_id(order_id: int | str) -> Cart | None:
    """Look up *order_id* without locking it, then lock its cart row.

    Must run inside ``transaction.atomic()`` BEFORE locking the ``Order``
    row so settlement takes locks in the same order as checkout
    (cart, then order).
    """
    from order.models.order import Order

    try:
        row = (
            Order.objects.filter(pk=order_id)
            .values("user_id", "metadata")
            .first()
        )
    except TypeError, ValueError:
        return None
    if row is None:
        return None
    return lock_cart_for_order_fields(
        user_id=row["user_id"], metadata=row["metadata"]
    )


def _ordered_cart_quantities(order: Order) -> dict[int, int]:
    """Product id -> units this order took from its cart.

    Online orders record the exact cart lines in
    ``metadata["checkout_fingerprint"]["lines"]`` at creation (excluding
    promotion gift lines injected onto the order). Prefer that map when
    present so a free gift of a product the shopper also kept in their cart
    never subtracts from their extra units; fall back to ``order.items`` for
    orders without a fingerprint.
    """
    fingerprint = (
        order.metadata.get("checkout_fingerprint")
        if isinstance(order.metadata, Mapping)
        else None
    )
    recorded = (
        fingerprint.get("lines") if isinstance(fingerprint, Mapping) else None
    )
    if isinstance(recorded, Mapping):
        parsed: dict[int, int] = {}
        try:
            for raw_id, raw_qty in recorded.items():
                product_id = int(raw_id)
                quantity = int(raw_qty)
                if quantity > 0:
                    parsed[product_id] = parsed.get(product_id, 0) + quantity
            return parsed
        except TypeError, ValueError:
            pass

    ordered: dict[int, int] = {}
    for product_id, quantity in order.items.values_list(
        "product_id", "quantity"
    ):
        if product_id is not None and quantity > 0:
            ordered[product_id] = ordered.get(product_id, 0) + quantity
    return ordered


def clear_locked_cart_for_order(order: Order, cart: Cart | None) -> None:
    """Take *order*'s lines out of the already-locked *cart*, and drop
    *cart* if that empties it.

    Not "delete the cart": an online order deliberately leaves the cart
    standing until the payment lands, and a hosted session can stay open
    for hours. Anything the shopper puts in the cart during that window is
    theirs, not part of the order being paid for, and deleting the row
    would lose it. The cart is a per-user singleton, so only the LINES
    distinguish the two.

    Lines the order has but the cart does not (promotion gifts injected at
    creation) are excluded when ``checkout_fingerprint.lines`` is recorded,
    and otherwise simply find nothing to remove.
    """
    if cart is None:
        return

    ordered = _ordered_cart_quantities(order)

    if ordered:
        for cart_item in (
            cart.items.select_for_update(of=("self",))
            .filter(product_id__in=ordered)
            .order_by("pk")
        ):
            take = ordered[cart_item.product_id]
            if cart_item.quantity > take:
                cart_item.quantity -= take
                cart_item.save(update_fields=["quantity"])
            else:
                cart_item.delete()

    if cart.items.exists():
        logger.info(
            "Cart %s kept after order %s — it holds items the order "
            "did not include",
            cart.uuid,
            order.id,
        )
        return

    cart.delete()
    logger.debug("Cleared cart %s after order %s", cart.uuid, order.id)


def clear_cart_for_order(order: Order) -> None:
    """Lock *order*'s cart and remove *order*'s lines from it.

    Used outside an online payment settlement (the post-commit empty-cart
    drop of offline orders in ``handle_order_created``, and direct tests).
    Online settlement paths lock the cart before the order and call
    :func:`clear_locked_cart_for_order` inside their own transaction.
    """
    with transaction.atomic():
        cart = lock_cart_for_order_fields(
            user_id=order.user_id, metadata=order.metadata
        )
        clear_locked_cart_for_order(order, cart)
