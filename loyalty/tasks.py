import logging

from core import celery_app
from core.tasks import MonitoredTask
from core.utils.tenant_urls import get_tenant_frontend_url

logger = logging.getLogger(__name__)


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def process_order_points(self, order_id: int) -> dict:
    """Award points for completed order. Idempotent — checks for existing EARN transactions."""
    from loyalty.services import LoyaltyService
    from order.models.order import Order

    try:
        order = Order.objects.select_related("user").get(id=order_id)
    except Order.DoesNotExist:
        logger.error("Order %s not found for loyalty points", order_id)
        return {"status": "error", "reason": "order_not_found"}

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


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def reverse_order_points(self, order_id: int) -> dict:
    """Reverse points for canceled/refunded order."""
    from loyalty.services import LoyaltyService
    from order.models.order import Order

    try:
        order = Order.objects.select_related("user").get(id=order_id)
    except Order.DoesNotExist:
        logger.error("Order %s not found for loyalty reversal", order_id)
        return {"status": "error", "reason": "order_not_found"}

    total_reversed = LoyaltyService.reverse_order_points(order_id)

    if order.user_id:
        LoyaltyService.recalculate_tier(order.user)

    return {
        "status": "success",
        "order_id": order_id,
        "points_reversed": total_reversed,
    }


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def process_points_expiration() -> dict:
    """Daily periodic task to expire old points."""
    from loyalty.services import LoyaltyService

    count = LoyaltyService.process_expiration()

    return {
        "status": "success",
        "transactions_created": count,
    }


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def recalculate_user_tier(self, user_id: int) -> dict:
    """Recalculate user tier after XP change."""
    from loyalty.services import LoyaltyService
    from user.models.account import UserAccount

    try:
        user = UserAccount.objects.get(id=user_id)
    except UserAccount.DoesNotExist:
        logger.error("User %s not found for tier recalculation", user_id)
        return {"status": "error", "reason": "user_not_found"}

    LoyaltyService.recalculate_tier(user)

    return {
        "status": "success",
        "user_id": user_id,
        "new_tier": str(user.loyalty_tier) if user.loyalty_tier else None,
    }


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def notify_loyalty_tier_up_live(self, user_id: int) -> dict:
    """Fire a celebratory live notification on a real tier promotion.

    The caller (loyalty signal handler) is already responsible for
    confirming the direction is ``"up"`` — we only re-read the user
    here to get a fresh tier name for the message body without trusting
    a stale name that was captured at dispatch time.
    """

    from loyalty.models.tier import LoyaltyTier
    from notification.enum import (
        NotificationCategoryEnum,
        NotificationKindEnum,
        NotificationTypeEnum,
    )
    from notification.services import create_user_notification
    from user.models.account import UserAccount

    try:
        user = UserAccount.objects.select_related("loyalty_tier").get(
            id=user_id
        )
    except UserAccount.DoesNotExist:
        logger.warning(
            "notify_loyalty_tier_up_live: user %s not found", user_id
        )
        return {"status": "skipped", "reason": "user_not_found"}

    tier: LoyaltyTier | None = user.loyalty_tier
    tier_name = (
        tier.safe_translation_getter("name", any_language=True) if tier else ""
    ) or ""

    loyalty_url = get_tenant_frontend_url("/account/loyalty")

    create_user_notification(
        user,
        kind=NotificationKindEnum.SUCCESS,
        category=NotificationCategoryEnum.PROMOTION,
        notification_type=NotificationTypeEnum.LOYALTY_TIER_UP,
        link=loyalty_url,
        translations={
            "en": {
                "title": f"You're now {tier_name}!"
                if tier_name
                else "Tier upgraded!",
                "message": (
                    "Your loyalty tier just went up — tap to see the new "
                    "benefits you've unlocked."
                ),
            },
            "el": {
                "title": (
                    f"Ανέβηκες στο {tier_name}!"  # noqa: RUF001
                    if tier_name
                    else "Νέο επίπεδο πιστότητας!"  # noqa: RUF001
                ),
                "message": (
                    "Το επίπεδο πιστότητάς σου ανέβηκε — δες τα νέα "  # noqa: RUF001
                    "προνόμια που ξεκλείδωσες."  # noqa: RUF001
                ),
            },
        },
    )
    return {"status": "sent", "user_id": user_id}
