import logging

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from blog.models import BlogComment
from notification.enum import NotificationKindEnum
from notification.models.notification import Notification
from notification.models.user import NotificationUser

logger = logging.getLogger(__name__)

User = get_user_model()

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


@receiver(m2m_changed, sender=BlogComment.likes.through)
def notify_comment_liked_receiver(
    sender, instance, action, reverse, pk_set, **kwargs
):
    if action == "post_add" and not reverse:
        async_to_sync(notify_comment_liked)(
            sender=sender,
            instance=instance,
            action=action,
            reverse=reverse,
            pk_set=pk_set,
            **kwargs,
        )


async def notify_comment_liked(
    sender, instance, action, reverse, pk_set, **kwargs
):
    try:
        if not pk_set:
            return

        user = await sync_to_async(getattr)(instance, "user", None)
        if not user:
            return

        post = await sync_to_async(getattr)(instance, "post", None)
        if not post:
            return

        blog_post_url = f"{settings.NUXT_BASE_URL}/blog/post/{post.id}/{post.slug}#blog-post-comments"

        for user_id in pk_set:
            if user.id == user_id:
                continue

            try:
                liker_user = await User.objects.aget(id=user_id)
                notification = await Notification.objects.acreate(
                    kind=NotificationKindEnum.INFO,
                    link=blog_post_url,
                )

                for language in languages:
                    await sync_to_async(notification.set_current_language)(
                        language
                    )

                    title = ""
                    message = ""

                    if language == "en":
                        title = f"<a href='{blog_post_url}'>Comment</a> Liked!"
                        message = (
                            f"Your comment was liked by "
                            f"{liker_user.username or liker_user.email}."
                        )
                    elif language == "el":
                        title = f"Το <a href='{blog_post_url}'>σχόλιο</a> σου πήρε like!"
                        message = (
                            f"Το σχόλιο σου άρεσε στον χρήστη "
                            f"{liker_user.username or liker_user.email}."
                        )

                    if title and message:
                        await sync_to_async(setattr)(
                            notification, "title", title
                        )
                        await sync_to_async(setattr)(
                            notification, "message", message
                        )
                        await notification.asave()

                await NotificationUser.objects.acreate(
                    user=user, notification=notification
                )

            except User.DoesNotExist:
                logger.error(f"User {user_id} does not exist")
                continue

            except Exception as e:
                logger.error(f"Error sending notification: {e}")
                continue

    except Exception as e:
        logger.error(f"Error sending notification: {e}")
        pass
