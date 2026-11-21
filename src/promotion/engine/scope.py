"""The cart lines a promotion covers: its scope, its exclusions, and the
category subtrees both name, expanded in one query."""

from __future__ import annotations

from promotion.enum import TargetScope
from promotion.models import Promotion


def attach_category_scopes(promotions: list[Promotion]) -> None:
    """Expand every promotion's categories from ONE descendant query
    and hang the result on the instance.

    The Promotion objects are built fresh by the caller for a single
    evaluation, so the attributes cannot outlive it or cross a
    tenant. ``ProductPromotionService`` needs the same expansion to
    decide whether a product's category is in scope, which is why
    this is public rather than inlined in ``collect_candidates``.
    """
    root_category_ids: set[int] = set()
    for promotion in promotions:
        root_category_ids.update(c.id for c in promotion.categories.all())
        root_category_ids.update(
            c.id for c in promotion.excluded_categories.all()
        )
    index = _descendant_index(root_category_ids)
    for promotion in promotions:
        promotion._included_category_ids = _expand(promotion.categories, index)
        promotion._excluded_category_ids = _expand(
            promotion.excluded_categories, index
        )


def _descendant_index(root_ids: set[int]) -> dict[int, set[int]]:
    """``category id -> its descendant ids``, for every root, in ONE query.

    This used to be a per-call ``get_descendants`` inside
    ``_expanded_category_ids``, and ``matching_items`` calls that
    for a category-scoped promotion's INCLUDED categories and for
    every promotion's EXCLUDED ones — so the engine still grew with
    the number of promotions carrying categories, which is the cost
    this change set exists to remove. Measured before: 13 queries at
    two category-scoped promotions, 25 at eight.

    A memo keyed on the id set would not have helped, because
    distinct promotions usually carry distinct categories. One query
    over the union does: MPTT gives every row a ``tree_id`` and an
    ``lft``/``rght`` range, so each root's descendants are
    recoverable from the same result set in memory.
    """
    if not root_ids:
        return {}
    from product.models.category import ProductCategory

    rows = list(
        ProductCategory.objects.filter(pk__in=root_ids)
        .get_descendants(include_self=True)
        .values("id", "tree_id", "lft", "rght")
    )
    roots = {row["id"]: row for row in rows if row["id"] in root_ids}
    return {
        root_id: {
            row["id"]
            for row in rows
            if row["tree_id"] == root["tree_id"]
            and root["lft"] <= row["lft"] <= root["rght"]
        }
        for root_id, root in roots.items()
    }


def _category_ids(promotion: Promotion, side: str) -> set[int]:
    """The expanded category ids ``collect_candidates`` attached.

    Falls back to expanding on the spot for a Promotion that did not
    come through candidate collection — the helper is also reachable
    from tests and from any future caller, and a silently empty set
    would quietly widen a promotion's scope rather than fail.
    """
    attr = f"_{side}_category_ids"
    cached = getattr(promotion, attr, None)
    if cached is not None:
        return cached

    source = (
        promotion.categories
        if side == "included"
        else promotion.excluded_categories
    )
    ids = {category.id for category in source.all()}
    return _expand(source, _descendant_index(ids))


def _expand(categories_qs, index: dict[int, set[int]]) -> set[int]:
    """Union the prefetched categories' descendants from the index."""
    ids = [category.id for category in categories_qs.all()]
    if not ids:
        return set()
    return set().union(*(index.get(cid, {cid}) for cid in ids))


def matching_items(promotion: Promotion, cart_items) -> list:
    """Cart items the promotion may count and discount.

    Scope first (ORDER = everything, PRODUCTS/CATEGORIES with MPTT
    descendants), then the exclusion set: excluded products,
    excluded categories (descendants included), and — when
    ``exclude_discounted_products`` — anything already carrying a
    product-level markdown. Exclusions shrink both the discount
    base AND condition counting (min_quantity), everywhere,
    consistently.
    """
    if promotion.target_scope == TargetScope.PRODUCTS:
        # `.all()`, not `.values_list()`: values_list builds a
        # new queryset and always queries, ignoring the prefetch.
        include_ids = {p.id for p in promotion.products.all()}
        items = [item for item in cart_items if item.product_id in include_ids]
    elif promotion.target_scope == TargetScope.CATEGORIES:
        include_categories = _category_ids(promotion, "included")
        items = [
            item
            for item in cart_items
            if item.product.category_id in include_categories
        ]
    else:
        items = list(cart_items)

    excluded_ids = {p.id for p in promotion.excluded_products.all()}
    if excluded_ids:
        items = [item for item in items if item.product_id not in excluded_ids]

    excluded_categories = _category_ids(promotion, "excluded")
    if excluded_categories:
        items = [
            item
            for item in items
            if item.product.category_id not in excluded_categories
        ]

    if promotion.exclude_discounted_products:
        items = [
            item
            for item in items
            if not item.product.discount_percent
            or item.product.discount_percent <= 0
        ]

    return items
