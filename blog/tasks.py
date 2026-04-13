import logging

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model

from notification.enum import NotificationKindEnum
from notification.models.notification import Notification
from notification.models.user import NotificationUser

logger = logging.getLogger(__name__)

User = get_user_model()

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


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
    """
    Create in-app notifications for users who liked a blog comment.

    Runs in a Celery worker to avoid async_to_sync deadlocks under ASGI
    (Daphne). Called from the m2m_changed signal in blog.signals.
    """
    for user_id in liker_user_ids:
        if user_id == comment_owner_id:
            continue

        try:
            liker_user = User.objects.get(id=user_id)
            comment_owner = User.objects.get(id=comment_owner_id)

            notification = Notification.objects.create(
                kind=NotificationKindEnum.INFO,
                link=blog_post_url,
            )

            for language in languages:
                notification.set_current_language(language)

                title = ""
                message = ""

                if language == "en":
                    title = f"<a href='{blog_post_url}'>Comment</a> Liked!"
                    message = (
                        f"Your comment was liked by "
                        f"{liker_user.username or liker_user.email}."
                    )
                elif language == "el":
                    title = (
                        f"Το <a href='{blog_post_url}'>σχόλιο</a>"
                        f" σου πήρε like!"
                    )
                    message = (
                        f"Το σχόλιο σου άρεσε στον χρήστη "
                        f"{liker_user.username or liker_user.email}."
                    )

                if title and message:
                    notification.title = title
                    notification.message = message
                    notification.save()

            NotificationUser.objects.create(
                user=comment_owner, notification=notification
            )

        except User.DoesNotExist:
            logger.error(
                "User %s does not exist in notify_comment_liked_task",
                user_id,
            )
        except Exception as e:
            logger.error(
                "Error sending comment-liked notification for user %s: %s",
                user_id,
                e,
                exc_info=True,
            )
