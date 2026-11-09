"""Which published documents a change to a record reaches, and the purge of
exactly those.

A page shows records through the typed data props of its blocks
(``builder.data_sources``), and the storefront caches the rendered page.
When a product, category, post or promotion changes, the pages that show it
must be purged, not only the product's own cache. ``BuilderReference`` (the
index of the CURRENT release) answers which: a page names a record
exactly (a ``product`` row), or asks for a list the record may enter or
leave (a ``queryProduct`` row whose target is a facet).

The receivers here work out the affected documents when a record is saved
or deleted, add them to a per-store Redis set after the transaction commits,
and mark the ``builder_documents`` surface stale. The batched purge
(``core.cache.invalidation``) then sends the storefront the document keys
(``builderDocuments``); the storefront maps a key to its own routes, so
Django never builds a page pattern.

Over-purging is safe and under-purging is the bug: a change is matched by ANY
facet of a list, and a facet is matched in the record's old and new values.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.db.models.signals import post_delete, post_save, pre_save

from builder.data_sources import ANY
from builder.models import BuilderReference, ReferenceKind
from core.publication import went_live

logger = logging.getLogger(__name__)

#: The cache surface the purge rides.
SURFACE = "builder_documents"
KEY = "builder-data:documents"
TTL_SECONDS = 600
#: The storefront takes at most this many document keys in one purge.
BATCH = 64

#: Saves that change nothing a page shows (the same rule as the product
#: surface: ``order/stock.py`` saves ``stock`` on every sale).
_IGNORED_UPDATE_FIELDS = frozenset({"stock", "updated_at"})


def documents_for(
    *,
    records: Iterable[tuple[str, int | str]] = (),
    lists: Iterable[tuple[str, Iterable[str]]] = (),
    any_record_of: Iterable[str] = (),
) -> set[str]:
    """The documents of the current release that name one of ``records``
    (``(kind, id)``), ask for one of ``lists`` (``(query kind, facets)``),
    or have any row of the kinds in ``any_record_of``."""
    condition = Q(pk__in=[])
    for kind, target in records:
        condition |= Q(kind=kind, target=str(target))
    for kind, facets in lists:
        condition |= Q(kind=kind, target__in=list(facets))
    for kind in any_record_of:
        condition |= Q(kind=kind)
    return set(
        BuilderReference.objects.filter(condition)
        .values_list("document", flat=True)
        .distinct()
    )


def record(documents: Iterable[str]) -> None:
    """Hand ``documents`` to the next batched purge."""
    ordered = sorted(set(documents))
    if not ordered:
        return
    try:
        cache.add_to_set(KEY, ordered, timeout=TTL_SECONDS)
        from core.cache.invalidation import mark_surface_stale

        mark_surface_stale(SURFACE)
    except Exception:
        logger.exception(
            "Builder documents %s could not be queued for a purge; their "
            "cached pages stay until the TTL",
            ordered,
        )


def take() -> list[str]:
    """The documents queued for a purge, taken."""
    return sorted(cache.pop_set(KEY))


def batches(documents: list[str]) -> list[list[str]]:
    return [
        documents[start : start + BATCH]
        for start in range(0, len(documents), BATCH)
    ]


# --- what each record means for the pages --------------------------------


def _category_facets(category_ids: Iterable[int | None]) -> set[str]:
    """``category:<id>`` for each category and each of its ancestors: a list
    asks for a category WITH its descendants."""
    from product.models.category import ProductCategory

    ids = {i for i in category_ids if i is not None}
    facets = {f"category:{i}" for i in ids}
    for category in ProductCategory.objects.filter(pk__in=ids):
        facets |= {
            f"category:{a.pk}"
            for a in category.get_ancestors(include_self=True)
        }
    return facets


def _stored(model_label: str, pk: int | None, field: str):
    """``field`` of the stored row, or ``None`` when it is gone."""
    if pk is None:
        return None
    return (
        apps.get_model(model_label)
        ._base_manager.filter(pk=pk)
        .values_list(field, flat=True)
        .first()
    )


def _product_documents(
    product_id: int, category_ids: Iterable[int | None]
) -> set[str]:
    return documents_for(
        records=[(ReferenceKind.PRODUCT, product_id)],
        lists=[
            (
                ReferenceKind.QUERY_PRODUCT,
                {ANY, *_category_facets(category_ids)},
            )
        ],
    )


def _category_documents(
    category_id: int, parent_ids: Iterable[int | None]
) -> set[str]:
    from product.models.category import ProductCategory

    category = ProductCategory.objects.filter(pk=category_id).first()
    around: set[int] = {category_id}
    if category is not None:
        around |= {n.pk for n in category.get_ancestors(include_self=True)} | {
            n.pk for n in category.get_descendants(include_self=True)
        }
    return documents_for(
        records=[(ReferenceKind.CATEGORY, category_id)],
        lists=[
            (
                ReferenceKind.QUERY_CATEGORY,
                {ANY}
                | {f"parent:{p}" for p in parent_ids if p is not None}
                | {f"parent:{category_id}"},
            ),
            (
                ReferenceKind.QUERY_PRODUCT,
                {f"category:{pk}" for pk in around},
            ),
        ],
    )


def _post_documents(
    post_id: int, category_ids: Iterable[int | None]
) -> set[str]:
    return documents_for(
        records=[(ReferenceKind.BLOG_POST, post_id)],
        lists=[
            (
                ReferenceKind.QUERY_BLOG_POST,
                {ANY}
                | {f"category:{c}" for c in category_ids if c is not None},
            )
        ],
    )


def _blog_category_documents(category_id: int) -> set[str]:
    return documents_for(
        records=[(ReferenceKind.BLOG_CATEGORY, category_id)],
        lists=[
            (ReferenceKind.QUERY_BLOG_CATEGORY, {ANY}),
            (ReferenceKind.QUERY_BLOG_POST, {f"category:{category_id}"}),
        ],
    )


def _promotion_documents() -> set[str]:
    """A promotion changes the discount badge and price of any card: every
    page that shows a product, as well as the pages that list offers."""
    return documents_for(
        lists=[(ReferenceKind.QUERY_PROMOTION, {ANY})],
        any_record_of=[ReferenceKind.PRODUCT, ReferenceKind.QUERY_PRODUCT],
    )


# --- the receivers --------------------------------------------------------


def _remember(attribute: str, model, field: str):
    """A ``pre_save`` that keeps the stored value of ``field`` on the
    instance, so the receiver after the save knows where the record WAS."""

    def receiver(sender, instance, **kwargs):
        if kwargs.get("raw") or instance.pk is None:
            return
        setattr(
            instance,
            attribute,
            model._base_manager.filter(pk=instance.pk)
            .values_list(field, flat=True)
            .first(),
        )

    return receiver


def _later(compute) -> None:
    """Work the affected documents out and queue them once the transaction
    has committed."""
    transaction.on_commit(lambda: record(compute()))


def _skip(kwargs: dict) -> bool:
    if kwargs.get("raw"):
        return True
    fields = kwargs.get("update_fields")
    return bool(fields and set(fields) <= _IGNORED_UPDATE_FIELDS)


_connections: list[tuple[object, object, str]] = []


def _connect(signal, receiver, sender, uid: str) -> None:
    _connections.append((signal, sender, uid))
    signal.connect(receiver, sender=sender, dispatch_uid=uid, weak=False)


def connect(*, force: bool = False) -> None:
    """Wire the receivers. Skipped when caching is disabled (nothing is
    cached, so nothing needs purging); ``force`` is for the tests of this
    module."""
    if getattr(settings, "DISABLE_CACHE", False) and not force:
        return

    product = apps.get_model("product.Product")
    product_tr = apps.get_model("product.ProductTranslation")
    category = apps.get_model("product.ProductCategory")
    category_tr = apps.get_model("product.ProductCategoryTranslation")
    post = apps.get_model("blog.BlogPost")
    post_tr = apps.get_model("blog.BlogPostTranslation")
    blog_category = apps.get_model("blog.BlogCategory")
    blog_category_tr = apps.get_model("blog.BlogCategoryTranslation")
    promotion = apps.get_model("promotion.Promotion")
    promotion_tr = apps.get_model("promotion.PromotionTranslation")

    def saved_product(sender, instance, **kwargs):
        if _skip(kwargs):
            return
        before = getattr(instance, "_builder_old_category", None)
        _later(
            lambda: _product_documents(
                instance.pk, [before, instance.category_id]
            )
        )

    def deleted_product(sender, instance, **kwargs):
        _later(lambda: _product_documents(instance.pk, [instance.category_id]))

    def product_translation(sender, instance, **kwargs):
        if _skip(kwargs):
            return
        master_id = instance.master_id
        _later(
            lambda: _product_documents(
                master_id,
                [_stored("product.Product", master_id, "category_id")],
            )
        )

    def saved_category(sender, instance, **kwargs):
        if _skip(kwargs):
            return
        before = getattr(instance, "_builder_old_parent", None)
        _later(
            lambda: _category_documents(
                instance.pk, [before, instance.parent_id]
            )
        )

    def category_translation(sender, instance, **kwargs):
        if _skip(kwargs):
            return
        master_id = instance.master_id
        _later(
            lambda: _category_documents(
                master_id,
                [_stored("product.ProductCategory", master_id, "parent_id")],
            )
        )

    def saved_post(sender, instance, **kwargs):
        if _skip(kwargs):
            return
        before = getattr(instance, "_builder_old_category", None)
        _later(
            lambda: _post_documents(instance.pk, [before, instance.category_id])
        )

    def deleted_post(sender, instance, **kwargs):
        _later(lambda: _post_documents(instance.pk, [instance.category_id]))

    def post_went_live(sender, instance, **kwargs):
        # A post dated in the future reached its date: nothing was saved,
        # so the receivers above never ran (``core.publication``).
        if sender is post:
            _later(lambda: _post_documents(instance.pk, [instance.category_id]))

    def post_translation(sender, instance, **kwargs):
        if _skip(kwargs):
            return
        master_id = instance.master_id
        _later(
            lambda: _post_documents(
                master_id,
                [_stored("blog.BlogPost", master_id, "category_id")],
            )
        )

    def blog_category_changed(sender, instance, **kwargs):
        if _skip(kwargs):
            return
        _later(lambda: _blog_category_documents(instance.pk))

    def blog_category_translation(sender, instance, **kwargs):
        if _skip(kwargs):
            return
        _later(lambda: _blog_category_documents(instance.master_id))

    def promotion_changed(sender, instance, **kwargs):
        if _skip(kwargs):
            return
        _later(_promotion_documents)

    _connect(
        pre_save,
        _remember("_builder_old_category", product, "category_id"),
        product,
        "builder-data:pre:product",
    )
    _connect(
        pre_save,
        _remember("_builder_old_parent", category, "parent_id"),
        category,
        "builder-data:pre:category",
    )
    _connect(
        pre_save,
        _remember("_builder_old_category", post, "category_id"),
        post,
        "builder-data:pre:post",
    )
    for signal, sender, receiver, uid in (
        (post_save, product, saved_product, "product"),
        (post_delete, product, deleted_product, "product"),
        (post_save, product_tr, product_translation, "product-tr"),
        (post_delete, product_tr, product_translation, "product-tr"),
        (post_save, category, saved_category, "category"),
        (post_delete, category, saved_category, "category"),
        (post_save, category_tr, category_translation, "category-tr"),
        (post_delete, category_tr, category_translation, "category-tr"),
        (post_save, post, saved_post, "post"),
        (post_delete, post, deleted_post, "post"),
        (post_save, post_tr, post_translation, "post-tr"),
        (post_delete, post_tr, post_translation, "post-tr"),
        (post_save, blog_category, blog_category_changed, "blog-category"),
        (post_delete, blog_category, blog_category_changed, "blog-category"),
        (
            post_save,
            blog_category_tr,
            blog_category_translation,
            "blog-category-tr",
        ),
        (
            post_delete,
            blog_category_tr,
            blog_category_translation,
            "blog-category-tr",
        ),
        (post_save, promotion, promotion_changed, "promotion"),
        (post_delete, promotion, promotion_changed, "promotion"),
        (post_save, promotion_tr, promotion_changed, "promotion-tr"),
        (post_delete, promotion_tr, promotion_changed, "promotion-tr"),
    ):
        _connect(
            signal,
            receiver,
            sender,
            f"builder-data:{'save' if signal is post_save else 'delete'}:{uid}",
        )
    _connect(went_live, post_went_live, None, "builder-data:went-live:post")


def disconnect() -> None:
    """Undo :func:`connect`, for the tests that wire it with ``force``."""
    for signal, sender, uid in _connections:
        signal.disconnect(sender=sender, dispatch_uid=uid)
    _connections.clear()
