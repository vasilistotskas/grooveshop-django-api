import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from core import celery_app
from core.tasks import MonitoredTask

logger = logging.getLogger(__name__)


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    name="Send Notification Task",
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def send_notification_task(self, data: dict, *args, **kwargs):
    user_id = data.get("user_id")
    if not user_id:
        logger.error("send_notification_task called without user_id")
        return

    channel_layer = get_channel_layer()

    async_to_sync(channel_layer.group_send)(
        f"user_{user_id}",
        {
            "type": "send_notification",
            "user": user_id,
            "id": data.get("id"),
            "seen": data.get("seen", False),
            "link": data.get("link", ""),
            "kind": data.get("kind", ""),
            "category": data.get("category", ""),
            "priority": data.get("priority", ""),
            "notification_type": data.get("notification_type", ""),
            "translations": data.get("translations", {}),
        },
    )
