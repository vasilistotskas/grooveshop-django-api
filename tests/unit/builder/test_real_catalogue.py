"""The storefront's real catalogue reads as Django expects it, links included.

``builder/seed_catalogue.json`` is the pinned copy of the storefront's
catalogue (``.github/scripts/sync_storefront_sources.py``). Every block and
document root must state ``linkRefs`` (``parse_catalogue`` refuses one that
does not), and what it states must be exactly the settings its own schema
marks as links (``x-builder-link``): a link the key leaves out would be stored
unindexed, its target unchecked on publish and missing from "where is it
used".
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from builder.media_migration import legacy_fields
from builder.seeding import SEED_CATALOGUE
from builder.storefront import parse_catalogue

#: The keyword the storefront puts on a link setting's JSON Schema.
LINK_MARKER = "x-builder-link"

PAYLOAD = json.loads(SEED_CATALOGUE.read_text(encoding="utf-8"))
CATALOGUE = parse_catalogue(PAYLOAD)


def _marked(schema: Any, pointer: str = "") -> Iterator[str]:
    """The pointers (``*`` for a list's elements) of the settings a schema
    marks as links, walked the way the storefront derives ``linkRefs``."""
    if not isinstance(schema, dict):
        return
    if schema.get(LINK_MARKER) is True:
        yield pointer
        return
    for name, sub in (schema.get("properties") or {}).items():
        escaped = name.replace("~", "~0").replace("/", "~1")
        yield from _marked(sub, f"{pointer}/{escaped}")
    yield from _marked(schema.get("items"), f"{pointer}/*")


def test_every_block_and_root_states_its_links():
    for raw in PAYLOAD["blocks"]:
        assert isinstance(raw.get("linkRefs"), list), raw["type"]
    for kind, raw in PAYLOAD["documentKinds"].items():
        assert isinstance(raw.get("linkRefs"), list), kind


@pytest.mark.parametrize("raw", PAYLOAD["blocks"], ids=lambda raw: raw["type"])
def test_a_blocks_links_are_the_settings_its_schema_marks(raw):
    block = CATALOGUE.get(raw["type"])

    assert block is not None
    assert sorted(block.link_refs) == sorted(_marked(raw["schema"]))


@pytest.mark.parametrize(
    "kind", sorted(PAYLOAD["documentKinds"]), ids=lambda kind: kind
)
def test_a_roots_links_are_the_settings_its_schema_marks(kind):
    raw = PAYLOAD["documentKinds"][kind]

    assert sorted(CATALOGUE.roots[kind].link_refs) == sorted(
        _marked(raw["schema"])
    )


def test_the_blocks_that_carry_a_link_say_so():
    """The run sheet used to check this by hand after a storefront deploy:
    a hero's link is a link."""
    assert CATALOGUE.get("hero_carousel").link_refs == ("/link",)
    assert set(CATALOGUE.get("features_grid").link_refs) == {
        "/ctaLink",
        "/prompt/ctaLink",
    }


def test_a_list_of_images_takes_an_asset_id_and_nothing_else():
    """Why ``media_migration._place_lists`` cannot keep an empty entry of an
    old list in its place: the list it becomes has no value for "no image".
    If this fails, the storefront has given such a list an empty slot, and the
    migration can keep one instead of stopping the store."""
    schemas = {raw["type"]: raw["schema"] for raw in PAYLOAD["blocks"]}
    lists = [
        (block, legacy.target)
        for block, fields in legacy_fields().items()
        for legacy in fields
        if legacy.is_list
    ]

    assert lists == [
        ("hero_carousel", "artworks"),
        ("hero_carousel", "mobileArtworks"),
    ]
    for block, target in lists:
        items = schemas[block]["properties"][target]["items"]
        assert (items["type"], items["minimum"]) == ("integer", 1)
