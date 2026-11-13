"""Bring the stored documents up to the current version of their blocks.

The storefront upgrades an older node when it reads one, and the editor when
it loads one (``shared/builder/*Upgrades.ts``), but Django accepts only the
current version of a block, so a draft nobody opened since a block moved
cannot be published, and a release written before a block took data settings
records no dependency rows for them. This module is the same upgrades, for
writing into what is stored: ``manage.py persist_block_upgrades``. A
translation is kept apart from its node, by setting pointer, so it moves with
it: :func:`upgrade_translations`.

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
from dataclasses import dataclass
from typing import Any

from builder import pointers
from builder.document_rewrite import DocumentRefused
from builder.published import localised
from builder.storefront import BlockDefinition, Catalogue
from builder.validation import VERSIONED_TYPE

Settings = dict[str, Any]
Upgrade = Callable[[Settings], Settings]


@dataclass(frozen=True)
class SchemaUpgrade:
    """An upgrade that reads its block's CURRENT schema, the catalogue's: what
    it may write is what that schema allows now, as the storefront's upgrade
    reads its own list as it is now."""

    run: Callable[[Settings, dict[str, Any]], Settings]


#: What the first product rails called their orderings, and what each sorted by.
LEGACY_PRODUCT_ORDERING = {
    "featured": "mostViewed",
    "newest": "newest",
    "popular": "mostLiked",
    "discounted": "discounted",
    "rating": "topRated",
}


class NoUpgradePath(DocumentRefused):
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


def _icon_names(schema: dict[str, Any]) -> list[str]:
    """The icons a block's ``icon`` setting takes: the enum of the schema the
    catalogue states for it (the storefront's ``BUILDER_ICONS``)."""
    icon = (schema.get("properties") or {}).get("icon") or {}
    names = icon.get("enum")
    if not isinstance(names, list):
        raise NoUpgradePath("the block's icon setting lists no icons")
    return [name for name in names if isinstance(name, str)]


def _icon_onto_list(settings: Settings, schema: dict[str, Any]) -> Settings:
    """An icon setting was free text and takes one of a list now: a saved name
    is written as its entry (``i-lucide:shield`` is ``i-lucide-shield``, which
    the icon sets read alike) and one the list does not hold is left out, the
    setting being optional. The storefront's ``upgradeIconSetting``."""
    icon = settings.get("icon")
    if not isinstance(icon, str):
        return settings
    listed = next(
        (
            name
            for name in _icon_names(schema)
            if name in (icon, icon.replace(":", "-", 1))
        ),
        None,
    )
    kept = {key: value for key, value in settings.items() if key != "icon"}
    return {**kept, "icon": listed} if listed else kept


#: Every block whose ``icon`` setting took the list, at the version it had.
_ICON_ONTO_LIST = SchemaUpgrade(_icon_onto_list)


def _icon_keeping_a_mark(fallback: str) -> SchemaUpgrade:
    """``_icon_onto_list`` for a block that needs a mark, an image, an image
    URL or an icon (``trust_badge``): when the icon it leaves out was the only
    one, *fallback* (the block's preset icon) takes its place, so the node
    still passes its block's rule. The storefront's
    ``upgradeIconSettingKeepingAMark``."""

    def upgrade(settings: Settings, schema: dict[str, Any]) -> Settings:
        upgraded = _icon_onto_list(settings, schema)
        dropped = (
            isinstance(settings.get("icon"), str) and "icon" not in upgraded
        )
        marked = "image" in upgraded or bool(upgraded.get("imageUrl"))
        return (
            {**upgraded, "icon": fallback}
            if dropped and not marked
            else upgraded
        )

    return SchemaUpgrade(upgrade)


def _title_into_heading(settings: Settings) -> Settings:
    """A band's heading was two settings, and every component drew ``heading``
    first: the one it drew stays, under the one name."""
    kept = dict(settings)
    title = kept.pop("title", None)
    heading = kept.pop("heading", None)
    text = heading or title
    return {**kept, "heading": text} if text else kept


#: ``{block type: {version: the upgrade to the next one}}``, with the arguments
#: of each block's ``upgrade`` in the storefront's ``app/blocks/*.block.ts``.
UPGRADES: dict[str, dict[int, Upgrade | SchemaUpgrade]] = {
    "featured_products": {
        1: _product_rail("featured", 8, 24),
        2: _title_into_heading,
    },
    "products_grid": {
        1: _product_rail("featured", 8, 24),
        2: _title_into_heading,
    },
    "products_slider": {
        1: _product_rail("newest", 8, 24),
        2: _title_into_heading,
    },
    "blog_posts_grid": {1: _blog_rail(3, 24), 2: _ICON_ONTO_LIST},
    "blog_posts_list": {1: _blog_rail(3, 24)},
    "blog_posts_carousel": {1: _blog_rail(6, 12)},
    "blog_categories": {1: _blog_categories(10)},
    "product_categories": {1: _product_categories(8)},
    "offers_preview": {1: _offers(3), 2: _title_into_heading},
    "hero_slide": {1: _hero_slide},
    "contact_panel": {1: _title_into_heading},
    "cta_banner": {1: _title_into_heading},
    "hero_banner": {1: _title_into_heading},
    "media_text": {1: _title_into_heading},
    "newsletter_signup": {1: _title_into_heading},
    "recently_viewed": {1: _title_into_heading},
    "story_timeline": {1: _title_into_heading},
    "testimonials": {1: _title_into_heading},
    "trust_badges": {1: _title_into_heading},
    "feature_item": {1: _ICON_ONTO_LIST},
    "footer_column": {1: _ICON_ONTO_LIST},
    "nav_link": {1: _ICON_ONTO_LIST},
    "timeline_entry": {1: _ICON_ONTO_LIST},
    "trust_badge": {1: _icon_keeping_a_mark("i-lucide-shield-check")},
}


def upgrade_node(
    node: dict[str, Any], definition: BlockDefinition
) -> dict[str, Any]:
    """*node* at the current version of its block, *definition*; its children
    are left to :func:`upgrade_document`, as the storefront's ``upgradeNode``
    leaves them to the walk over the document."""
    current = definition.version
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
        settings = (
            step.run(settings, definition.schema)
            if isinstance(step, SchemaUpgrade)
            else step(settings)
        )
    return {**node, "type": f"{name}@{current}", "settings": settings}


def upgrade_translations(
    node: dict[str, Any],
    definition: BlockDefinition,
    translations: dict[str, Any],
) -> dict[str, Any]:
    """One language's *translations* of *node*, once it is at its block's
    current version. What a language reads is the upgrade of its own settings,
    so a translation follows whatever an upgrade does to its setting (a rename,
    a merge, the setting taken out) and no upgrade has to state it. An entry is
    kept where the language reads something other than the draft's upgraded
    text, or where it already translated that setting; a new entry that only
    repeats the draft's text is left out, since the setting falls back to that
    text anyway. The storefront's ``upgradeNodeTranslations``, held to the same
    cases."""
    upgraded = upgrade_node(node, definition)
    if upgraded is node:
        return translations
    translated = upgrade_node(
        {**node, "settings": localised(node["settings"], translations)},
        definition,
    )
    moved: dict[str, Any] = {}
    for declared in definition.translatable:
        pattern = pointers.parse(declared)
        draft = dict(pointers.walk(upgraded["settings"], pattern))
        for path, value in pointers.walk(translated["settings"], pattern):
            pointer = pointers.format_path(path)
            if value not in ("", []) and (
                pointer in translations or value != draft.get(path)
            ):
                moved[pointer] = value
    return moved


def nodes_of(holder: dict[str, Any], key: str) -> Iterator[tuple[dict, str]]:
    """``(the mapping that holds it, its id)`` of every node under *key*."""
    for node_id, node in (holder.get(key) or {}).items():
        yield holder[key], node_id
        if isinstance(node, dict):
            yield from nodes_of(node, "blocks")


def upgrade_document(
    data: dict[str, Any], i18n: dict[str, Any], catalogue: Catalogue
) -> tuple[dict[str, Any], dict[str, Any], Counter[str]]:
    """Copies of document *data* with every node at its block's current version
    and of its overlay *i18n* moved with them, and how many nodes of each older
    ``type@version`` moved. A node of a block the catalogue does not list stays
    as it is: that is for the validation to refuse, not for an upgrade to guess
    at."""
    upgraded = copy.deepcopy(data)
    before: dict[str, tuple[dict[str, Any], BlockDefinition]] = {}
    moved: Counter[str] = Counter()
    for parent, node_id in list(nodes_of(upgraded, "sections")):
        node = parent[node_id]
        versioned = node.get("type") if isinstance(node, dict) else None
        if not isinstance(versioned, str):
            continue
        match = VERSIONED_TYPE.match(versioned)
        definition = catalogue.get(match.group(1)) if match else None
        if definition is None or int(match.group(2)) >= definition.version:
            continue
        before[node_id] = (node, definition)
        parent[node_id] = upgrade_node(node, definition)
        moved[versioned] += 1
    return upgraded, _upgraded_overlay(i18n, before), moved


def _upgraded_overlay(
    i18n: dict[str, Any],
    before: dict[str, tuple[dict[str, Any], BlockDefinition]],
) -> dict[str, Any]:
    """A copy of overlay *i18n* with the nodes in *before* moved; a node or a
    language left with no translation goes. What is not a moved node (a node
    already current, the document's own settings) stays as it is."""
    upgraded: dict[str, Any] = {}
    for locale, per_node in i18n.items():
        kept: dict[str, Any] = {}
        for node_id, translations in per_node.items():
            entries = (
                upgrade_translations(*before[node_id], translations)
                if node_id in before
                else copy.deepcopy(translations)
            )
            if entries:
                kept[node_id] = entries
        if kept:
            upgraded[locale] = kept
    return upgraded
