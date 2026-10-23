"""Gift-card reactions to order lifecycle signals.

A CANCELED order that was (partly) settled by gift cards gives the
redemption back to the source card(s) as REFUND_CREDIT ledger rows. A
REFUND does not go through a signal: ``OrderService.refund_order`` pays
the provider first and credits the cards with the value beyond it
(``GiftCardService.credit_refund``), in parts, as staff enter it.
"""

import logging
from typing import Any

from django.dispatch import receiver

from order.models.order import Order
from order.signals import order_canceled
from tenant.celery import dispatch_on_commit
from tenant.permissions import IsGiftCardsRuntimeEnabled
from tenant.switches import on_switch_change

logger = logging.getLogger(__name__)


def _queue_refund_credit(order: Order) -> None:
    """Queue the credit of the order's whole gift-card redemption."""
    if not (order.gift_card_amount and order.gift_card_amount.amount > 0):
        return
    from giftcard.tasks import credit_refund_to_gift_cards

    dispatch_on_commit(credit_refund_to_gift_cards, [order.id])


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


# Gift cards switched off or on: record the pause, or close it and move
# active cards' expiry forward by it (``GiftCardService.sync_pause``).
on_switch_change(
    setting_keys=(IsGiftCardsRuntimeEnabled.setting_key,),
    plan_flags=("gift_cards_enabled",),
    task="giftcard.tasks.sync_gift_card_pause",
)
