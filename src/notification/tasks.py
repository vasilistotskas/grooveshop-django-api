import logging

from channels.layers import get_channel_layer
from django.conf import settings

from core import celery_app
from core.retry import TRANSIENT_ERRORS
from core.tasks import MonitoredTask
from notification.groups import send_to_user_group

logger = logging.getLogger(__name__)


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    name="Send Notification Task",
    max_retries=3,
    # ``send_to_user_group`` raises the builtin ``TimeoutError`` at its
    # deadline, which ``TRANSIENT_ERRORS`` already retries.
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    soft_time_limit=settings.NOTIFICATION_TASK_SOFT_TIME_LIMIT,
    time_limit=settings.NOTIFICATION_TASK_TIME_LIMIT,
)
def send_notification_task(self, data: dict, *args, **kwargs):
    user_id = data.get("user_id")
    if not user_id:
        logger.error("send_notification_task called without user_id")
        return

    channel_layer = get_channel_layer()
    if not channel_layer:
        logger.error("send_notification_task: channel layer is not configured")
        return

    # The group is named after the schema active on this worker
    # connection.  The task is dispatched via TenantTask (or MonitoredTask
    # with a _schema_name header) so connection.schema_name is set
    # correctly by the time we run.
    send_to_user_group(
        channel_layer,
        user_id,
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
