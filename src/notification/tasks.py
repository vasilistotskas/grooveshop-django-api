import logging

from channels.layers import get_channel_layer
from django.conf import settings
from django.utils import timezone

from core import celery_app
from core.monitored_task import MonitoredTask
from core.retry import TRANSIENT_ERRORS
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
    """Push one ``NotificationUser`` to its user's sockets, once.

    The storefront shows a toast for every frame, and a copy of this task
    (a retry, a message delivered again, the sweep's copy of a publish)
    would push the same row again. So the push first claims the row
    (``pushed_at``, a compare-and-set): a copy that finds it claimed pushes
    nothing. A push that fails gives the claim back for the retry. A worker
    that dies between the claim and the push loses that toast; the
    notification itself stays in the user's list.
    """
    from notification.models.user import NotificationUser

    user_id = data.get("user_id")
    row_id = data.get("id")
    if not user_id or not row_id:
        logger.error("send_notification_task called without user_id or id")
        return

    channel_layer = get_channel_layer()
    if not channel_layer:
        logger.error("send_notification_task: channel layer is not configured")
        return

    unpushed = NotificationUser.objects.filter(
        pk=row_id, pushed_at__isnull=True
    )
    if not unpushed.update(pushed_at=timezone.now()):
        logger.info(
            "send_notification_task: notification %s was already pushed",
            row_id,
        )
        return

    # The group is named after the schema active on this worker
    # connection.  The task is dispatched via TenantTask (or MonitoredTask
    # with a _schema_name header) so connection.schema_name is set
    # correctly by the time we run.
    try:
        send_to_user_group(
            channel_layer,
            user_id,
            {
                "type": "send_notification",
                "user": user_id,
                "id": row_id,
                "seen": data.get("seen", False),
                "link": data.get("link", ""),
                "kind": data.get("kind", ""),
                "category": data.get("category", ""),
                "priority": data.get("priority", ""),
                "notification_type": data.get("notification_type", ""),
                "translations": data.get("translations", {}),
            },
        )
    except BaseException:
        NotificationUser.objects.filter(pk=row_id).update(pushed_at=None)
        raise
