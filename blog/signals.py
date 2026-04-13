import logging

from django.conf import settings
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from blog.models import BlogComment

logger = logging.getLogger(__name__)


@receiver(m2m_changed, sender=BlogComment.likes.through)
def notify_comment_liked_receiver(
    sender, instance, action, reverse, pk_set, **kwargs
):
    if action != "post_add" or reverse or not pk_set:
        return

    from blog.tasks import notify_comment_liked_task

    user = getattr(instance, "user", None)
    if not user:
        return

    post = getattr(instance, "post", None)
    if not post:
        return

    blog_post_url = (
        f"{settings.NUXT_BASE_URL}/blog/post/{post.id}/{post.slug}"
        "#blog-post-comments"
    )

    notify_comment_liked_task.delay(
        comment_id=instance.id,
        liker_user_ids=list(pk_set),
        blog_post_url=blog_post_url,
        comment_owner_id=user.id,
    )
