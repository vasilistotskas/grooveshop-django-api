import logging
from typing import Any

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import Signal, receiver

from order.models.order import Order
from order.signals import order_canceled, order_completed, order_refunded
from user.models.account import UserAccount

logger = logging.getLogger(__name__)


# Fires when ``UserAccount.loyalty_tier`` actually changes value.
# Kwargs: ``user``, ``old_tier_id`` (may be None on first promotion),
# ``new_tier_id`` (may be None on SET_NULL), ``direction`` (``"up"`` |
# ``"down"`` | ``"same"``). Downstream notification tasks use the
# direction to decide whether to celebrate the user or stay silent —
# nobody wants a toast telling them they lost a tier.
loyalty_tier_changed = Signal()


@receiver(
    order_completed, dispatch_uid="loyalty.handle_order_completed_loyalty"
)
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

            order_id = order.id
            transaction.on_commit(lambda: process_order_points.delay(order_id))
    except Exception:
        logger.exception(
            "Failed to queue loyalty points for order %s", order.id
        )


@receiver(order_canceled, dispatch_uid="loyalty.handle_order_canceled_loyalty")
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

            order_id = order.id
            transaction.on_commit(lambda: reverse_order_points.delay(order_id))
    except Exception:
        logger.exception(
            "Failed to queue loyalty reversal for order %s", order.id
        )


@receiver(order_refunded, dispatch_uid="loyalty.handle_order_refunded_loyalty")
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

            order_id = order.id
            transaction.on_commit(lambda: reverse_order_points.delay(order_id))
    except Exception:
        logger.exception(
            "Failed to queue loyalty reversal for order %s", order.id
        )


@receiver(
    pre_save,
    sender=UserAccount,
    dispatch_uid="loyalty.cache_original_loyalty_tier",
)
def cache_original_loyalty_tier(
    sender: type[UserAccount], instance: UserAccount, **kwargs: Any
) -> None:
    """Snapshot the pre-save tier id so the post_save handler can
    detect transitions without issuing a second query.

    On inserts (``pk`` not yet set) the snapshot is ``None`` — the
    post_save branch treats this as "no previous tier" and skips
    dispatch, so newly-created users who happen to spawn with a tier
    don't get an unexpected "tier up" notification.
    """
    if not instance.pk:
        instance._original_loyalty_tier_id = None
        return
    try:
        old = sender.objects.only("loyalty_tier_id").get(pk=instance.pk)
        instance._original_loyalty_tier_id = old.loyalty_tier_id
    except sender.DoesNotExist:
        instance._original_loyalty_tier_id = None


@receiver(
    post_save,
    sender=UserAccount,
    dispatch_uid="loyalty.dispatch_tier_changed",
)
def dispatch_tier_changed(
    sender: type[UserAccount],
    instance: UserAccount,
    created: bool,
    **kwargs: Any,
) -> None:
    """Fire ``loyalty_tier_changed`` on real tier transitions.

    Skips creates (no previous tier to compare against) and no-op saves
    (tier id unchanged). Direction is derived from tier ``level`` so
    downstream listeners don't need to fetch tiers themselves.
    """
    if created:
        return
    if not hasattr(instance, "_original_loyalty_tier_id"):
        return
    old_id = instance._original_loyalty_tier_id
    new_id = instance.loyalty_tier_id
    if old_id == new_id:
        return

    direction = "same"
    try:
        from loyalty.models.tier import LoyaltyTier

        # ``LoyaltyTier.required_level`` is the canonical ordering
        # field — higher means more prestigious. Missing tier
        # (SET_NULL scenario) is treated as the lowest level so a
        # no-tier → any-tier transition counts as "up".
        old_level = (
            LoyaltyTier.objects.only("required_level")
            .get(pk=old_id)
            .required_level
            if old_id
            else -1
        )
        new_level = (
            LoyaltyTier.objects.only("required_level")
            .get(pk=new_id)
            .required_level
            if new_id
            else -1
        )
        if new_level > old_level:
            direction = "up"
        elif new_level < old_level:
            direction = "down"
    except Exception:
        logger.exception(
            "Failed to resolve tier direction for user %s (%s -> %s)",
            instance.pk,
            old_id,
            new_id,
        )

    def send_tier_changed() -> None:
        loyalty_tier_changed.send(
            sender=sender,
            user=instance,
            old_tier_id=old_id,
            new_tier_id=new_id,
            direction=direction,
        )
        logger.debug(
            "Sent loyalty_tier_changed for user %s (%s -> %s, %s)",
            instance.pk,
            old_id,
            new_id,
            direction,
        )

    transaction.on_commit(send_tier_changed)


@receiver(
    loyalty_tier_changed,
    dispatch_uid="loyalty.notify_tier_up_live",
)
def notify_tier_up_live(
    sender: type[UserAccount],
    user: UserAccount,
    direction: str,
    **kwargs: Any,
) -> None:
    """Celebrate tier promotions with a live notification.

    Tier downgrades are intentionally silent — notifying someone that
    they've *lost* status is gratuitously negative. Lateral moves
    (``"same"``) shouldn't happen in practice, but we guard against
    them defensively.
    """
    if direction != "up":
        return
    from loyalty.tasks import notify_loyalty_tier_up_live

    user_id = user.pk
    transaction.on_commit(lambda: notify_loyalty_tier_up_live.delay(user_id))
