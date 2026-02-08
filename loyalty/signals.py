import logging
from typing import Any

from django.dispatch import receiver

from order.models.order import Order
from order.signals import order_canceled, order_completed, order_refunded

logger = logging.getLogger(__name__)


@receiver(order_completed)
def handle_order_completed_loyalty(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """Trigger async points earning when order completes.

    Queues a Celery task to calculate and award loyalty points
    for the completed order. Only processes orders with an
    authenticated user (skips guest orders).
    """
    try:
        if order.user_id:
            from loyalty.tasks import process_order_points

            process_order_points.delay_on_commit(order.id)
    except Exception:
        logger.exception(
            "Failed to queue loyalty points for order %s", order.id
        )


@receiver(order_canceled)
def handle_order_canceled_loyalty(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """Trigger async points reversal when order is canceled.

    Queues a Celery task to reverse any loyalty points that were
    previously earned for this order. Only processes orders with
    an authenticated user.
    """
    try:
        if order.user_id:
            from loyalty.tasks import reverse_order_points

            reverse_order_points.delay_on_commit(order.id)
    except Exception:
        logger.exception(
            "Failed to queue loyalty reversal for order %s", order.id
        )


@receiver(order_refunded)
def handle_order_refunded_loyalty(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """Trigger async points reversal when order is refunded.

    Queues a Celery task to reverse any loyalty points that were
    previously earned for this order. Only processes orders with
    an authenticated user.
    """
    try:
        if order.user_id:
            from loyalty.tasks import reverse_order_points

            reverse_order_points.delay_on_commit(order.id)
    except Exception:
        logger.exception(
            "Failed to queue loyalty reversal for order %s", order.id
        )
