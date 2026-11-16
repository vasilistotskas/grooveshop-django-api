"""The storefront's real catalogue reads as Django expects it, links included.

``builder/catalogues/<design>.json`` are the pinned copies of the
storefront's catalogue as each design is served it
(``.github/scripts/sync_upstream_sources.py``). Every block and document
root must state ``linkRefs`` (``parse_catalogue`` refuses one that does not),
and what it states must be exactly the settings its own schema marks as
links (``x-builder-link``): a link the key leaves out would be stored
unindexed, its target unchecked on publish and missing from "where is it
used". Each design is checked: a store reads only its own.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from builder.media_migration import legacy_fields
from tests.utils.real_catalogues import DESIGNS, block, catalogue, payload

#: The keyword the storefront puts on a link setting's JSON Schema.
LINK_MARKER = "x-builder-link"

BLOCKS = [
    (design, raw) for design in DESIGNS for raw in payload(design)["blocks"]
]
ROOTS = [
    (design, kind)
    for design in DESIGNS
    for kind in sorted(payload(design)["documentKinds"])
]


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


@pytest.mark.parametrize("design", DESIGNS)
def test_every_block_and_root_states_its_links(design):
    for raw in payload(design)["blocks"]:
        assert isinstance(raw.get("linkRefs"), list), raw["type"]
    for kind, raw in payload(design)["documentKinds"].items():
        assert isinstance(raw.get("linkRefs"), list), kind


@pytest.mark.parametrize(
    ("design", "raw"),
    BLOCKS,
    ids=[f"{design}-{raw['type']}" for design, raw in BLOCKS],
)
def test_a_blocks_links_are_the_settings_its_schema_marks(design, raw):
    definition = catalogue(design).get(raw["type"])

    assert definition is not None
    assert sorted(definition.link_refs) == sorted(_marked(raw["schema"]))


@pytest.mark.parametrize(
    ("design", "kind"), ROOTS, ids=[f"{d}-{k}" for d, k in ROOTS]
)
def test_a_roots_links_are_the_settings_its_schema_marks(design, kind):
    raw = payload(design)["documentKinds"][kind]

    assert sorted(catalogue(design).roots[kind].link_refs) == sorted(
        _marked(raw["schema"])
    )


def test_the_blocks_that_carry_a_link_say_so():
    """The run sheet used to check this by hand after a storefront deploy:
    a hero's link is a link."""
    assert block("hero_carousel").link_refs == ("/link",)
    assert set(block("features_grid").link_refs) == {
        "/ctaLink",
        "/prompt/ctaLink",
    }


def test_a_list_of_images_takes_an_asset_id_and_nothing_else():
    """Why ``media_migration._place_lists`` cannot keep an empty entry of an
    old list in its place: the list it becomes has no value for "no image".
    If this fails, the storefront has given such a list an empty slot, and the
    migration can keep one instead of stopping the store."""
    schemas = {raw["type"]: raw["schema"] for _, raw in BLOCKS}
    lists = [
        (type_name, legacy.target)
        for type_name, fields in legacy_fields().items()
        for legacy in fields
        if legacy.is_list
    ]

    assert lists == [
        ("hero_carousel", "artworks"),
        ("hero_carousel", "mobileArtworks"),
    ]
    for type_name, target in lists:
        items = schemas[type_name]["properties"][target]["items"]
        assert (items["type"], items["minimum"]) == ("integer", 1)
