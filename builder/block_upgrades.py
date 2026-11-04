"""Bring the stored documents up to the current version of their blocks.

The storefront upgrades an older node when it reads one, and the editor when
it loads one (``shared/builder/*Upgrades.ts``), but Django accepts only the
current version of a block, so a draft nobody opened since a block moved
cannot be published, and a release written before a block took data settings
records no dependency rows for them. This module is the same upgrades, for
writing into what is stored: ``manage.py persist_block_upgrades``.

They are written twice on purpose. Django cannot run the storefront's
TypeScript, and the catalogue states a block's current version, not how to
reach it. ``tests/fixtures/builder/upgrade-cases.json`` is the storefront's
list of cases, a pinned copy its own spec proves against the real blocks;
``tests/unit/builder/test_block_upgrades.py`` holds this port to it, so a
change on one side that the other does not make fails one of the two suites.
"""

from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Callable, Iterator
from typing import Any

from builder.storefront import Catalogue
from builder.validation import VERSIONED_TYPE

Settings = dict[str, Any]
Upgrade = Callable[[Settings], Settings]

#: What the first product rails called their orderings, and what each sorted by.
LEGACY_PRODUCT_ORDERING = {
    "featured": "mostViewed",
    "newest": "newest",
    "popular": "mostLiked",
    "discounted": "discounted",
    "rating": "topRated",
}


class NoUpgradePath(Exception):
    """A node is older than its block and nothing here brings it up."""


def _is_number(value: Any) -> bool:
    """JavaScript's ``typeof value === 'number'``: a bool is an ``int`` here."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _product_rail(
    default_ordering: str, default_limit: int, cap: int
) -> Upgrade:
    def upgrade(settings: Settings) -> Settings:
        kept = dict(settings)
        ordering = kept.pop("ordering", None)
        category = kept.pop("categoryId", None)
        size = kept.pop("pageSize", None)
        kept.pop("columns", None)
        known = (
            isinstance(ordering, str) and ordering in LEGACY_PRODUCT_ORDERING
        )
        products: dict[str, Any] = {"mode": "filter"}
        if _is_number(category):
            products["categories"] = [category]
        products["ordering"] = LEGACY_PRODUCT_ORDERING[
            ordering if known else default_ordering
        ]
        products["limit"] = min(
            size if _is_number(size) else default_limit, cap
        )
        return {**kept, "products": products}

    return upgrade


def _blog_rail(default_limit: int, cap: int) -> Upgrade:
    def upgrade(settings: Settings) -> Settings:
        kept = dict(settings)
        category = kept.pop("categoryId", None)
        count = kept.pop("count", None)
        page_size = kept.pop("pageSize", None)
        size = next(
            (value for value in (page_size, count) if _is_number(value)),
            default_limit,
        )
        posts: dict[str, Any] = {"mode": "filter"}
        if _is_number(category):
            posts["category"] = category
        posts["ordering"] = "newest"
        posts["limit"] = min(size, cap)
        return {**kept, "posts": posts}

    return upgrade


def _blog_categories(limit: int) -> Upgrade:
    def upgrade(settings: Settings) -> Settings:
        return {**settings, "categories": {"limit": limit}}

    return upgrade


def _product_categories(default_limit: int) -> Upgrade:
    def upgrade(settings: Settings) -> Settings:
        kept = dict(settings)
        parent = kept.pop("parentId", None)
        limit = kept.pop("limit", None)
        size = limit if _is_number(limit) else default_limit
        categories = (
            {"mode": "children", "parent": parent, "limit": size}
            if _is_number(parent)
            else {"mode": "roots", "limit": size}
        )
        return {**kept, "categories": categories}

    return upgrade


def _offers(default_limit: int) -> Upgrade:
    def upgrade(settings: Settings) -> Settings:
        kept = dict(settings)
        limit = kept.pop("limit", None)
        size = limit if _is_number(limit) else default_limit
        return {**kept, "offers": {"limit": size}}

    return upgrade


def _hero_slide(settings: Settings) -> Settings:
    kept = dict(settings)
    if "productId" in kept:
        kept["product"] = kept.pop("productId")
    return kept


#: ``{block type: {version: the upgrade to the next one}}``, with the arguments
#: of each block's ``upgrade`` in the storefront's ``app/blocks/*.block.ts``.
UPGRADES: dict[str, dict[int, Upgrade]] = {
    "featured_products": {1: _product_rail("featured", 8, 24)},
    "products_grid": {1: _product_rail("featured", 8, 24)},
    "products_slider": {1: _product_rail("newest", 8, 24)},
    "blog_posts_grid": {1: _blog_rail(3, 24)},
    "blog_posts_list": {1: _blog_rail(3, 24)},
    "blog_posts_carousel": {1: _blog_rail(6, 12)},
    "blog_categories": {1: _blog_categories(10)},
    "product_categories": {1: _product_categories(8)},
    "offers_preview": {1: _offers(3)},
    "hero_slide": {1: _hero_slide},
}


def upgrade_node(node: dict[str, Any], current: int) -> dict[str, Any]:
    """*node* at version *current* of its block; its children are left to
    :func:`upgrade_document`, as the storefront's ``upgradeNode`` leaves them
    to the walk over the document."""
    match = VERSIONED_TYPE.match(node["type"])
    if match is None:
        raise NoUpgradePath(f"{node['type']} is not <type>@<version>")
    name, written = match.group(1), int(match.group(2))
    if written >= current:
        return node
    settings = node["settings"]
    for version in range(written, current):
        step = UPGRADES.get(name, {}).get(version)
        if step is None:
            raise NoUpgradePath(f"no upgrade from {name}@{version}")
        settings = step(settings)
    return {**node, "type": f"{name}@{current}", "settings": settings}


def _nodes(holder: dict[str, Any], key: str) -> Iterator[tuple[dict, str]]:
    for node_id, node in (holder.get(key) or {}).items():
        yield holder[key], node_id
        if isinstance(node, dict):
            yield from _nodes(node, "blocks")


def upgrade_document(
    data: dict[str, Any], catalogue: Catalogue
) -> tuple[dict[str, Any], Counter[str]]:
    """A copy of document *data* with every node at its block's current
    version, and how many nodes of each older ``type@version`` moved. A node
    of a block the catalogue does not list stays as it is: that is for the
    validation to refuse, not for an upgrade to guess at."""
    upgraded = copy.deepcopy(data)
    moved: Counter[str] = Counter()
    for parent, node_id in list(_nodes(upgraded, "sections")):
        node = parent[node_id]
        versioned = node.get("type") if isinstance(node, dict) else None
        if not isinstance(versioned, str):
            continue
        match = VERSIONED_TYPE.match(versioned)
        definition = catalogue.get(match.group(1)) if match else None
        if definition is None or int(match.group(2)) >= definition.version:
            continue
        parent[node_id] = upgrade_node(node, definition.version)
        moved[versioned] += 1
    return upgraded, moved
