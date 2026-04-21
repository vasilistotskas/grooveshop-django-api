import logging

from celery import shared_task
from django.contrib.auth import get_user_model

from notification.enum import (
    NotificationCategoryEnum,
    NotificationKindEnum,
    NotificationTypeEnum,
)
from notification.services import create_user_notification

logger = logging.getLogger(__name__)

User = get_user_model()


@shared_task(
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
            translations={
                "en": {
                    "title": (f"<a href='{blog_post_url}'>Comment</a> Liked!"),
                    "message": (f"Your comment was liked by {liker_label}."),
                },
                "el": {
                    "title": (
                        f"Το <a href='{blog_post_url}'>σχόλιο</a>"
                        f" σου πήρε like!"
                    ),
                    "message": (
                        f"Το σχόλιο σου άρεσε στον χρήστη {liker_label}."
                    ),
                },
            },
        )
