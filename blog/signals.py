import logging

from django.conf import settings
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from blog.models import BlogComment
from blog.models.post import BlogPost
from core.publication import went_live
from core.utils.tenant_urls import storefront_path
from tenant.celery import dispatch_on_commit

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
    _reindex_translations(instance)


@receiver(went_live, dispatch_uid="blog.index_post_gone_live")
def index_post_gone_live(sender, instance, **kwargs):
    """A post dated in the future whose date passed: nothing was saved,
    so the post_save receiver above never indexed it."""
    if sender is BlogPost:
        _reindex_translations(instance)


def _reindex_translations(post: BlogPost) -> None:
    if settings.MEILISEARCH.get("OFFLINE", False):
        return

    # No import guard: both modules are in this project and celery is a
    # hard dependency, so the import cannot fail. Swallowing it meant a
    # real breakage would have silently skipped indexing altogether,
    # leaving stale documents searchable with nothing logged.
    from blog.models.post import BlogPostTranslation
    from meili.tasks import index_document_task

    # A plain query: ``get_meilisearch_queryset()`` carries the likes
    # COUNT join, which every save would run just to collect the pks
    # (the product receiver's G0307 lesson).
    translation_pks = list(
        BlogPostTranslation.objects.filter(master=post).values_list(
            "pk", flat=True
        )
    )
    if not translation_pks:
        return

    for pk in translation_pks:
        dispatch_on_commit(
            index_document_task,
            kwargs={
                "app_label": "blog",
                "model_name": "blogposttranslation",
                "pk": pk,
            },
        )


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

    blog_post_url = storefront_path(
        f"{post.storefront_path}#blog-post-comments"
    )

    dispatch_on_commit(
        notify_comment_liked_task,
        kwargs={
            "comment_id": instance.id,
            "liker_user_ids": list(pk_set),
            "blog_post_url": blog_post_url,
            "comment_owner_id": user.id,
        },
    )
