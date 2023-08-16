from celery import shared_task
from celery.utils.log import get_task_logger

from core import caches
from core.caches import cache_instance

logger = get_task_logger(__name__)


@shared_task(bind=True, name="Clear Sessions For None Users Task")
def clear_sessions_for_none_users_task():
    for key in cache_instance.keys(caches.USER_AUTHENTICATED + "_*"):
        if key.split("_")[1] == "NONE":
            cache_instance.delete(key)

    cache_instance.set(caches.CLEAR_SESSIONS_FOR_NONE_USERS_TASK, True, caches.ONE_HOUR)
    logger.info("Clear Sessions For None Users Task Completed")


@shared_task(bind=True, name="Clear Carts For None Users Task")
def clear_carts_for_none_users_task():
    from cart.models import Cart

    null_carts = Cart.objects.filter(user=None)
    null_carts.delete()
    logger.info("Clear Carts For None Users Task Completed")
