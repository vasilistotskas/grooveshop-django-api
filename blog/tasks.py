import logging

from django.contrib.auth import get_user_model

from core import celery_app
from core.tasks import MonitoredTask
from notification.enum import (
    NotificationCategoryEnum,
    NotificationKindEnum,
    NotificationTypeEnum,
)
from notification.services import create_user_notification

logger = logging.getLogger(__name__)

User = get_user_model()


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    name="notify_comment_liked",
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def notify_comment_liked_task(
    self,
    comment_id: int,
    liker_user_ids: list[int],
    blog_post_url: str,
    comment_owner_id: int,
) -> None:
    """Create in-app notifications for users who liked a blog comment.

    Runs in a Celery worker to avoid async_to_sync deadlocks under ASGI
    (Daphne). Called from the m2m_changed signal in blog.signals.
    """
    try:
        comment_owner = User.objects.get(id=comment_owner_id)
    except User.DoesNotExist:
        logger.error(
            "notify_comment_liked_task: comment owner %s not found",
            comment_owner_id,
        )
        return

    for user_id in liker_user_ids:
        if user_id == comment_owner_id:
            continue

        try:
            liker = User.objects.get(id=user_id)
        except User.DoesNotExist:
            logger.error(
                "notify_comment_liked_task: liker %s not found", user_id
            )
            continue

        liker_label = liker.username or liker.email
        create_user_notification(
            comment_owner,
            kind=NotificationKindEnum.INFO,
            category=NotificationCategoryEnum.REVIEW,
            notification_type=NotificationTypeEnum.COMMENT_LIKED,
            link=blog_post_url,
            # Plain text — the ``link`` carries the blog-post URL and
            # the UI treats the notification card as a single tap
            # target, so ``<a>`` tags in the copy would render as
            # literal HTML.
            translations={
                "en": {
                    "title": "Your comment was liked",
                    "message": (
                        f"{liker_label} liked your comment. "
                        f"Tap to see the thread."
                    ),
                },
                "el": {
                    "title": "Το σχόλιό σου πήρε like!",  # noqa: RUF001
                    "message": (
                        f"Στον χρήστη {liker_label} άρεσε το σχόλιό σου. "  # noqa: RUF001
                        f"Πάτα εδώ για να δεις τη συζήτηση."  # noqa: RUF001
                    ),
                },
            },
        )
