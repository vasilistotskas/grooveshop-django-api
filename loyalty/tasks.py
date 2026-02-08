import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=5, default_retry_delay=300)
def process_order_points(self, order_id: int) -> dict:
    """Award points for completed order. Idempotent — checks for existing EARN transactions."""
    from loyalty.services import LoyaltyService
    from order.models.order import Order

    try:
        order = Order.objects.select_related("user").get(id=order_id)
    except Order.DoesNotExist:
        logger.error("Order %s not found for loyalty points", order_id)
        return {"status": "error", "reason": "order_not_found"}

    try:
        total_points = LoyaltyService.award_order_points(order_id)

        bonus_points = 0
        if total_points > 0 and order.user_id:
            bonus_points = LoyaltyService.check_new_customer_bonus(
                order.user, order
            )
            LoyaltyService.recalculate_tier(order.user)

        return {
            "status": "success",
            "order_id": order_id,
            "points_awarded": total_points,
            "bonus_points": bonus_points,
        }
    except Exception as e:
        logger.error(
            "Error processing loyalty points for order #%s: %s",
            order_id,
            str(e),
            extra={"order_id": order_id, "error": str(e)},
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e
        return {"status": "error", "reason": str(e)}


@shared_task(bind=True, max_retries=5, default_retry_delay=300)
def reverse_order_points(self, order_id: int) -> dict:
    """Reverse points for canceled/refunded order."""
    from loyalty.services import LoyaltyService
    from order.models.order import Order

    try:
        order = Order.objects.select_related("user").get(id=order_id)
    except Order.DoesNotExist:
        logger.error("Order %s not found for loyalty reversal", order_id)
        return {"status": "error", "reason": "order_not_found"}

    try:
        total_reversed = LoyaltyService.reverse_order_points(order_id)

        if order.user_id:
            LoyaltyService.recalculate_tier(order.user)

        return {
            "status": "success",
            "order_id": order_id,
            "points_reversed": total_reversed,
        }
    except Exception as e:
        logger.error(
            "Error reversing loyalty points for order #%s: %s",
            order_id,
            str(e),
            extra={"order_id": order_id, "error": str(e)},
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e
        return {"status": "error", "reason": str(e)}


@shared_task
def process_points_expiration() -> dict:
    """Daily periodic task to expire old points."""
    from loyalty.services import LoyaltyService

    try:
        count = LoyaltyService.process_expiration()

        return {
            "status": "success",
            "transactions_created": count,
        }
    except Exception as e:
        logger.error(
            "Error processing points expiration: %s",
            str(e),
            extra={"error": str(e)},
        )
        return {"status": "error", "reason": str(e)}


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def recalculate_user_tier(self, user_id: int) -> dict:
    """Recalculate user tier after XP change."""
    from loyalty.services import LoyaltyService
    from user.models.account import UserAccount

    try:
        user = UserAccount.objects.get(id=user_id)
    except UserAccount.DoesNotExist:
        logger.error("User %s not found for tier recalculation", user_id)
        return {"status": "error", "reason": "user_not_found"}

    try:
        LoyaltyService.recalculate_tier(user)

        return {
            "status": "success",
            "user_id": user_id,
            "new_tier": str(user.loyalty_tier) if user.loyalty_tier else None,
        }
    except Exception as e:
        logger.error(
            "Error recalculating tier for user #%s: %s",
            user_id,
            str(e),
            extra={"user_id": user_id, "error": str(e)},
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e
        return {"status": "error", "reason": str(e)}
