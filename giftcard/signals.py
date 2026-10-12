"""Gift-card reactions to order lifecycle signals.

When an order that was (partly) settled by gift cards is refunded, the
gift-card portion goes BACK to the source card(s) as REFUND_CREDIT
ledger rows — the provider refund only ever covers the provider-charged
remainder.
"""

import logging
from typing import Any

from django.dispatch import receiver

from order.enum.status import PaymentStatus
from order.models.order import Order
from order.signals import order_canceled, order_refunded
from tenant.celery import dispatch_on_commit

logger = logging.getLogger(__name__)


def _queue_refund_credit(order: Order) -> None:
    """Queue the credit of the order's whole gift-card redemption."""
    if not (order.gift_card_amount and order.gift_card_amount.amount > 0):
        return
    from giftcard.tasks import credit_refund_to_gift_cards

    dispatch_on_commit(credit_refund_to_gift_cards, [order.id])


@receiver(
    order_refunded, dispatch_uid="giftcard.handle_order_refunded_giftcard"
)
def handle_order_refunded_giftcard(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    # The redemption goes back once, with the LAST refund. A partial
    # refund returns the provider's share first, so a slice of the
    # redemption on top of it would hand back more than that refund was
    # worth (a EUR 5 goodwill refund on an order settled with a EUR 60
    # card put EUR 60 back), and the one-credit-per-card ledger could not
    # top the card up to the whole when the rest is refunded.
    if order.payment_status != PaymentStatus.REFUNDED:
        return
    try:
        _queue_refund_credit(order)
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
