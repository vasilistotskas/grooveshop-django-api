"""Gift-card reactions to order lifecycle signals.

When an order that was (partly) settled by gift cards is refunded, the
gift-card portion goes BACK to the source card(s) as REFUND_CREDIT
ledger rows — the provider refund only ever covers the provider-charged
remainder.
"""

import logging
from typing import Any

from django.db import connection, transaction
from django.dispatch import receiver

from order.models.order import Order
from order.signals import order_canceled, order_refunded

logger = logging.getLogger(__name__)


def _queue_refund_credit(order: Order, amount=None) -> None:
    """Queue the gift-card credit for a refunded/canceled order.

    *amount* is how much was actually refunded; ``None`` means the whole
    order. Passing it matters: crediting the full redemption for a
    PARTIAL refund puts money on the card that was never returned.
    """
    if not (order.gift_card_amount and order.gift_card_amount.amount > 0):
        return
    from giftcard.tasks import credit_refund_to_gift_cards

    order_id = order.id
    # A Celery argument has to survive JSON, and `Money` does not.
    refunded = (
        None if amount is None else str(getattr(amount, "amount", amount))
    )
    # Stamp the tenant schema NOW: on_commit fires after the request's
    # schema context can unwind (loyalty/signals.py precedent).
    schema = connection.schema_name
    transaction.on_commit(
        lambda: credit_refund_to_gift_cards.apply_async(
            args=[order_id, refunded], headers={"_schema_name": schema}
        )
    )


@receiver(
    order_refunded, dispatch_uid="giftcard.handle_order_refunded_giftcard"
)
def handle_order_refunded_giftcard(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    # `amount` is part of the `order_refunded` contract: absent means a
    # FULL refund. Dropping it here is what credited the whole
    # redemption back for a five-euro goodwill refund.
    try:
        _queue_refund_credit(order, kwargs.get("amount"))
    except Exception:
        logger.exception(
            "Failed to queue gift-card refund credit for order %s", order.id
        )


@receiver(
    order_canceled, dispatch_uid="giftcard.handle_order_canceled_giftcard"
)
def handle_order_canceled_giftcard(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """A canceled order releases its gift-card funds the same way."""
    try:
        _queue_refund_credit(order)
    except Exception:
        logger.exception(
            "Failed to queue gift-card cancel credit for order %s", order.id
        )
