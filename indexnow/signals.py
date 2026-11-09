"""Queue the storefront pages whose content changed.

A product or a blog post is on the storefront while it is published
(``active`` and not deleted; a post is ``is_live``: published, its date not
in the future). A save of a published page queues its path, so does the save
that unpublishes it, so does the removal of the row, and a save of one of its
translations. Everything else (a draft edited, a product no one can see, a
post dated in the future) queues nothing: the page does not exist for a
search engine, and its address must not leave the store early.

A post dated in the future is queued when its date passes, by
``core.publication``'s sweep (``went_live``): nothing is saved then.

A save writes the queue row in its own transaction, so a change that rolls
back is never announced. ``queryset.update()`` emits no signal and is
therefore not announced either (the soft-delete and restore querysets of
``core.models``).
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import DatabaseError, transaction
from django.db.models.signals import post_delete, post_save, pre_save

from blog.models.post import BlogPost, BlogPostTranslation
from core.publication import went_live
from indexnow.service import is_enabled, queue
from product.models.product import Product, ProductTranslation

logger = logging.getLogger(__name__)

#: A save that touches only these is bookkeeping, not a change of the page.
_BOOKKEEPING_FIELDS = frozenset({"view_count", "updated_at"})

_PAGES: tuple[type, ...] = (Product, BlogPost)

_WAS_PUBLISHED = "_indexnow_was_published"


def _is_published(page: Product | BlogPost) -> bool:
    """Whether the storefront serves *page* now."""
    if isinstance(page, BlogPost):
        return page.is_live
    return page.active and not page.is_deleted


def _queue(page: Product | BlogPost) -> None:
    """Queue *page*'s path without letting a failed queue write fail the
    save of the page: the announcement is the optional part."""
    try:
        with transaction.atomic():
            queue([page.storefront_path])
    except DatabaseError:
        logger.exception(
            "IndexNow: could not queue a changed page",
            extra={"path": page.storefront_path},
        )


def _remember_publication(sender: type, instance: Any, **kwargs) -> None:
    """Before a save that leaves the page unpublished: was it published
    until now? Only that save needs the stored row."""
    setattr(instance, _WAS_PUBLISHED, False)
    if instance._state.adding or _is_published(instance) or not is_enabled():
        return
    stored = sender._base_manager.filter(pk=instance.pk).first()
    setattr(
        instance,
        _WAS_PUBLISHED,
        stored is not None and _is_published(stored),
    )


def _queue_saved(
    sender: type, instance: Any, update_fields=None, **kwargs
) -> None:
    if update_fields and set(update_fields) <= _BOOKKEEPING_FIELDS:
        return
    if (
        _is_published(instance) or getattr(instance, _WAS_PUBLISHED, False)
    ) and is_enabled():
        _queue(instance)


def _queue_deleted(sender: type, instance: Any, **kwargs) -> None:
    if _is_published(instance) and is_enabled():
        _queue(instance)


def _queue_saved_translation(sender: type, instance: Any, **kwargs) -> None:
    if is_enabled() and _is_published(instance.master):
        _queue(instance.master)


def _queue_went_live(sender: type, instance: Any, **kwargs) -> None:
    """A page dated in the future whose date just passed."""
    if sender in _PAGES and is_enabled() and _is_published(instance):
        _queue(instance)


went_live.connect(_queue_went_live, dispatch_uid="indexnow.went_live")

for _page in _PAGES:
    _name = _page.__name__.lower()
    pre_save.connect(
        _remember_publication,
        sender=_page,
        dispatch_uid=f"indexnow.{_name}_pre_save",
    )
    post_save.connect(
        _queue_saved, sender=_page, dispatch_uid=f"indexnow.{_name}_saved"
    )
    post_delete.connect(
        _queue_deleted, sender=_page, dispatch_uid=f"indexnow.{_name}_deleted"
    )

for _translation in (ProductTranslation, BlogPostTranslation):
    post_save.connect(
        _queue_saved_translation,
        sender=_translation,
        dispatch_uid=f"indexnow.{_translation.__name__.lower()}_saved",
    )
