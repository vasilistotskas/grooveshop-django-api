"""What a document points at, and whether it still exists.

A setting the catalogue marks as a reference stores an id, never a copy:
a product id, a category id, a page slug, a media id. The
storefront resolves it at render and shows an empty state for a target
that is gone, so a dangling reference never breaks a page, but publishing
one is refused (``check_exists``): a merchant must not release a block
that points at nothing.

``BuilderReference`` is the same information for the CURRENT release as a
table, so the product, category and page admins can say where a thing is
used before it is deleted.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any

from django.db import transaction

from builder.models import (
    BuilderDocument,
    BuilderReference,
    BuilderVersion,
    DocumentKind,
    ReferenceKind,
)

#: ``{kind: {target id: [node pointers]}}``, what a version records.
Refs = dict[str, dict[str, list[str]]]


MAX_ID = 2**63 - 1  # a bigint primary key
_MAX_NAME = 64  # a page or content page slug, as ``BuilderReference.target``


def accepts_value(kind: str, value: Any) -> bool:
    """Whether ``value`` has the shape of a ``kind`` reference: a numeric
    id for products, categories, blog posts and categories and media; a
    non-empty name otherwise. ``False`` for a kind that is not one."""
    if kind not in ReferenceKind.values:
        return False
    if kind in {
        ReferenceKind.PRODUCT,
        ReferenceKind.CATEGORY,
        ReferenceKind.BLOG_CATEGORY,
        ReferenceKind.BLOG_POST,
        ReferenceKind.MEDIA,
    }:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 < value <= MAX_ID
        )
    return isinstance(value, str) and 0 < len(value) <= _MAX_NAME


def _existing_products(targets: set[str]) -> set[str]:
    from product.models.product import Product

    return {
        str(pk)
        for pk in Product.objects.active()
        .filter(pk__in=[int(t) for t in targets])
        .values_list("pk", flat=True)
    }


def _existing_categories(targets: set[str]) -> set[str]:
    from product.models.category import ProductCategory

    return {
        str(pk)
        for pk in ProductCategory.objects.filter(
            pk__in=[int(t) for t in targets], active=True
        ).values_list("pk", flat=True)
    }


def _existing_blog_categories(targets: set[str]) -> set[str]:
    from blog.models.category import BlogCategory

    return {
        str(pk)
        for pk in BlogCategory.objects.filter(
            pk__in=[int(t) for t in targets]
        ).values_list("pk", flat=True)
    }


def _existing_blog_posts(targets: set[str]) -> set[str]:
    """A post the public blog shows: published, not scheduled for later."""
    from blog.models.post import BlogPost

    return {
        str(pk)
        for pk in BlogPost.objects.published()
        .filter(pk__in=[int(t) for t in targets])
        .values_list("pk", flat=True)
    }


def _existing_pages(targets: set[str]) -> set[str]:
    """A page is named by its key: the store's ``page:<key>`` document or
    the ``template:<key>`` of a fixed page type (home, about, ...), draft
    or released. Whether it is released is the storefront's to check when
    it renders the link."""
    keys = {
        key
        for target in targets
        for key in (
            f"{DocumentKind.PAGE}:{target}",
            f"{DocumentKind.TEMPLATE}:{target}",
        )
    }
    return {
        key.partition(":")[2]
        for key in BuilderDocument.objects.filter(key__in=keys).values_list(
            "key", flat=True
        )
    }


def _existing_content_pages(targets: set[str]) -> set[str]:
    """A content page (a policy, an info page) by slug, in any state: a
    menu may link a page before the store publishes it."""
    from page_config.models import ContentPage

    return set(
        ContentPage.objects.filter(slug__in=targets).values_list(
            "slug", flat=True
        )
    )


def _existing_media(targets: set[str]) -> set[str]:
    from builder.models import MediaAsset

    return {
        str(pk)
        for pk in MediaAsset.objects.filter(
            pk__in=[int(t) for t in targets]
        ).values_list("pk", flat=True)
    }


_EXISTS: dict[str, Callable[[set[str]], set[str]]] = {
    ReferenceKind.PRODUCT: _existing_products,
    ReferenceKind.CATEGORY: _existing_categories,
    ReferenceKind.BLOG_CATEGORY: _existing_blog_categories,
    ReferenceKind.BLOG_POST: _existing_blog_posts,
    ReferenceKind.PAGE: _existing_pages,
    ReferenceKind.CONTENT_PAGE: _existing_content_pages,
    ReferenceKind.MEDIA: _existing_media,
}


def missing(refs: Refs) -> dict[str, set[str]]:
    """The targets of ``refs`` that do not exist in this store, by kind.
    One query per kind, whatever the number of references."""
    return {
        kind: gone
        for kind, targets in refs.items()
        if (gone := set(targets) - _EXISTS[kind](set(targets)))
    }


def merge(refs: Iterable[tuple[str, str, str]]) -> Refs:
    out: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for kind, target, pointer in refs:
        out[kind][target].append(pointer)
    return {kind: dict(targets) for kind, targets in out.items()}


def rebuild_index(versions: dict[str, int]) -> None:
    """Replace ``BuilderReference`` with what the release's versions
    point at. ``versions`` is the release map ``{document key: version id}``."""
    rows = [
        BuilderReference(
            kind=kind, target=target, document=key, node_path=pointer
        )
        for key, refs in BuilderVersion.objects.filter(
            pk__in=versions.values()
        ).values_list("document__key", "refs")
        for kind, targets in refs.items()
        for target, pointers in targets.items()
        for pointer in pointers
    ]
    with transaction.atomic():
        BuilderReference.objects.all().delete()
        BuilderReference.objects.bulk_create(rows)


def used_by(kind: str, target: str | int) -> list[str]:
    """Keys of the documents of the current release that use a thing."""
    return sorted(
        set(
            BuilderReference.objects.filter(
                kind=kind, target=str(target)
            ).values_list("document", flat=True)
        )
    )


#: The kinds whose links the storefront turns into a path, and what names
#: that path: a product's, category's, post's and blog category's slug.
LINK_TARGET_KINDS = (
    ReferenceKind.PRODUCT,
    ReferenceKind.CATEGORY,
    ReferenceKind.BLOG_POST,
    ReferenceKind.BLOG_CATEGORY,
)


def link_targets(
    ids: dict[ReferenceKind, set[int]],
) -> dict[ReferenceKind, dict[str, str]]:
    """``{kind: {id: slug}}`` for the ids that exist and are visible: the
    same rule as the existence checks above, so what a publish accepted
    resolves here. An id that is missing, inactive or unpublished is
    absent. One query per kind asked about."""
    from blog.models.category import BlogCategory
    from blog.models.post import BlogPost
    from product.models.category import ProductCategory
    from product.models.product import Product

    querysets = {
        ReferenceKind.PRODUCT: lambda: Product.objects.active(),
        ReferenceKind.CATEGORY: lambda: ProductCategory.objects.filter(
            active=True
        ),
        ReferenceKind.BLOG_POST: lambda: BlogPost.objects.published(),
        ReferenceKind.BLOG_CATEGORY: lambda: BlogCategory.objects.all(),
    }
    return {
        kind: {
            str(pk): slug
            for pk, slug in querysets[kind]()
            .filter(pk__in=wanted)
            .values_list("pk", "slug")
        }
        for kind, wanted in ids.items()
    }
