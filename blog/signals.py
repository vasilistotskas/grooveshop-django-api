from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from blog.models import BlogComment
from notification.enum import NotificationKindEnum
from notification.models.notification import Notification
from notification.models.user import NotificationUser

User = get_user_model()

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


@receiver(m2m_changed, sender=BlogComment.likes.through)
async def notify_comment_liked(
    sender, instance, action, reverse, pk_set, **kwargs
):
    if action == "post_add" and not reverse:
        post = await sync_to_async(lambda: instance.post)()
        user = await sync_to_async(lambda: instance.user)()
        blog_post_url = f"{settings.NUXT_BASE_URL}/blog/post/{post.id}/{post.slug}#blog-post-comments"

        for user_id in pk_set:
            if user and user.id != user_id:
                liker_user = await User.objects.aget(id=user_id)
                notification = await Notification.objects.acreate(
                    kind=NotificationKindEnum.INFO,
                    link=blog_post_url,
                )

                for language in languages:
                    await sync_to_async(notification.set_current_language)(
                        language
                    )
                    if language == "en":
                        await sync_to_async(setattr)(
                            notification,
                            "title",
                            f"<a href='{blog_post_url}'>Comment</a> Liked!",
                        )
                        await sync_to_async(setattr)(
                            notification,
                            "message",
                            f"Your comment was liked by "
                            f"{liker_user.username if liker_user.username else liker_user.email}.",
                        )
                    elif language == "el":
                        await sync_to_async(setattr)(
                            notification,
                            "title",
                            f"Το <a href="  # noqa: RUF001
                            f"'"
                            f"{blog_post_url}'>σχόλιο</a> "
                            f"σου πήρε like!",  # noqa: RUF001
                        )
                        await sync_to_async(setattr)(
                            notification,
                            "message",
                            f"Το σχόλιο σου άρεσε στον χρήστη "  # noqa: RUF001
                            f"{liker_user.username if liker_user.username else liker_user.email}.",
                        )
                    await notification.asave()

                await NotificationUser.objects.acreate(
                    user=user, notification=notification
                )
