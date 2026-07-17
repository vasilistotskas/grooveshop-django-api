import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from blog.models import BlogComment
from blog.models.post import BlogPost

logger = logging.getLogger(__name__)


@receiver(
    post_save,
    sender=BlogPost,
    dispatch_uid="blog.reindex_blog_post_translations",
)
def reindex_blog_post_translations(sender, instance, **kwargs):
    """Reindex a post's translations when the parent BlogPost is saved.

    BlogPostTranslation is the Meilisearch-indexed model, so a change on the
    parent post (notably ``is_published`` flipping) must re-dispatch indexing
    for each translation — otherwise unpublishing a post leaves stale,
    still-searchable documents behind. ``index_document_task`` indexes
    translations that still match ``meili_filter()`` and deletes those that no
    longer do. Mirrors ``product.signals.reindex_product_translations``.
    """
    if settings.MEILISEARCH.get("OFFLINE", False):
        return

    try:
        from blog.models.post import BlogPostTranslation
        from meili.tasks import index_document_task
    except ImportError:
        return

    translation_pks = list(
        BlogPostTranslation.get_meilisearch_queryset()
        .filter(master=instance)
        .values_list("pk", flat=True)
    )
    if not translation_pks:
        return

    def _dispatch_reindex(pks=translation_pks):
        for pk in pks:
            index_document_task.delay(
                app_label="blog",
                model_name="blogposttranslation",
                pk=pk,
            )

    transaction.on_commit(_dispatch_reindex)


@receiver(
    m2m_changed,
    sender=BlogComment.likes.through,
    dispatch_uid="blog.notify_comment_liked_receiver",
)
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
