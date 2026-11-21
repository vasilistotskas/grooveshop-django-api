import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from cart.models import Cart
from core import celery_app
from core.monitored_task import MonitoredTask
from core.retry import TRANSIENT_ERRORS
from core.store_settings import store_setting

logger = logging.getLogger(__name__)


@celery_app.task(
    base=MonitoredTask,
    name="core.tasks.cleanup_abandoned_carts",
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
)
def cleanup_abandoned_carts():
    try:
        days = store_setting("ABANDONED_CART_CLEANUP_DAYS")
        cutoff_date = timezone.now() - timedelta(days=days)

        with transaction.atomic():
            count, _ = Cart.objects.filter(
                user__isnull=True,
                last_activity__lt=cutoff_date,
                items__isnull=True,
            ).delete()

        message = (
            f"Cleaned up {count} abandoned guest carts"
            if count > 0
            else "No abandoned carts to clean up"
        )
        logger.info(message)

        return {
            "status": "success",
            "message": message,
            "deleted_count": count,
            "cutoff_date": cutoff_date.isoformat(),
            "timestamp": timezone.now().isoformat(),
        }
    except Exception:
        logger.exception("Error cleaning up abandoned carts")
        raise


@celery_app.task(
    base=MonitoredTask,
    name="core.tasks.cleanup_old_guest_carts",
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
)
def cleanup_old_guest_carts():
    try:
        days = store_setting("OLD_GUEST_CART_CLEANUP_DAYS")
        cutoff_date = timezone.now() - timedelta(days=days)

        # Delete ALL sufficiently-old guest carts, empty or not. The
        # 7-day ``cleanup_abandoned_carts`` job already sweeps *empty*
        # guest carts; without dropping the ``items__isnull`` filter here
        # a guest cart that still held items after a tab-close / cookie
        # clear was never deleted by anything and lingered forever. Safe
        # to remove non-empty ones at 30 days: guests get no abandoned-
        # cart email (that flow is authenticated-only), the recovery page
        # is auth-gated, and any StockReservations are long past their
        # 15-minute TTL by then.
        count, _ = Cart.objects.filter(
            user__isnull=True,
            last_activity__lt=cutoff_date,
        ).delete()

        message = (
            f"Cleaned up {count} old guest carts ({days}+ days inactive)"
            if count > 0
            else "No old guest carts to clean up"
        )
        logger.info(message)

        return {
            "status": "success",
            "message": message,
            "deleted_count": count,
            "cutoff_date": cutoff_date.isoformat(),
            "timestamp": timezone.now().isoformat(),
        }
    except Exception:
        logger.exception("Error cleaning up old guest carts")
        raise
