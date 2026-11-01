"""What a document points at, and whether it still exists.

A setting the catalogue marks as a reference stores an id, never a copy:
a product id, a category id, a page slug, a menu slot, a media id. The
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


_MAX_ID = 2**63 - 1  # a bigint primary key
_MAX_NAME = 64  # a page slug or a menu slot, as ``BuilderReference.target``


def accepts_value(kind: str, value: Any) -> bool:
    """Whether ``value`` has the shape of a ``kind`` reference: a numeric
    id for products, categories and media; a non-empty name otherwise."""
    if kind in {
        ReferenceKind.PRODUCT,
        ReferenceKind.CATEGORY,
        ReferenceKind.MEDIA,
    }:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 < value <= _MAX_ID
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


def _existing_pages(targets: set[str]) -> set[str]:
    """A page is a published content page or a page the store built."""
    from page_config.models import ContentPage

    found = set(
        ContentPage.objects.published()
        .filter(slug__in=targets)
        .values_list("slug", flat=True)
    )
    built = {
        key.removeprefix(f"{DocumentKind.PAGE}:")
        for key in BuilderDocument.objects.filter(
            key__in=[f"{DocumentKind.PAGE}:{t}" for t in targets]
        ).values_list("key", flat=True)
    }
    return found | built


def _existing_menus(targets: set[str]) -> set[str]:
    from page_config.models import NavigationMenu

    return set(
        NavigationMenu.objects.filter(slot__in=targets).values_list(
            "slot", flat=True
        )
    )


def _existing_media(targets: set[str]) -> set[str]:
    # The media library lands with ``MediaAsset``; until then nothing a
    # document could name exists.
    return set()


_EXISTS: dict[str, Callable[[set[str]], set[str]]] = {
    ReferenceKind.PRODUCT: _existing_products,
    ReferenceKind.CATEGORY: _existing_categories,
    ReferenceKind.PAGE: _existing_pages,
    ReferenceKind.MENU: _existing_menus,
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
