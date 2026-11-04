"""The block upgrades exist twice, and both must agree.

The storefront upgrades a node when it reads one (``upgradeNode``); this
module's port writes the same upgrades into what is stored.
``upgrade-cases.json`` is the storefront's list of cases, a pinned copy
(``.github/scripts/sync_upstream_sources.py``) its own spec proves against the
real blocks. Running the port over the same cases means a change on one side
that the other does not make fails one of the two suites.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from builder.block_upgrades import (
    UPGRADES,
    NoUpgradePath,
    upgrade_document,
    upgrade_node,
)
from builder.storefront import parse_catalogue

CASES: list[dict[str, Any]] = json.loads(
    (
        Path(__file__).parents[2]
        / "fixtures"
        / "builder"
        / "upgrade-cases.json"
    ).read_text(encoding="utf-8")
)


def catalogue(**versions: int):
    return parse_catalogue(
        {
            "hash": "h",
            "blocks": [
                {
                    "type": name,
                    "version": version,
                    "schema": {"type": "object"},
                    "linkRefs": [],
                    "kinds": ["section"],
                }
                for name, version in versions.items()
            ],
        }
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_a_node_upgrades_as_the_storefront_upgrades_it(case):
    assert upgrade_node(case["before"], 2) == case["after"]


def test_every_upgrade_here_has_a_case():
    covered = {case["before"]["type"].partition("@")[0] for case in CASES}

    assert set(UPGRADES) <= covered


def test_a_node_at_its_current_version_is_left_alone():
    node = {"type": "offers_preview@2", "settings": {"offers": {"limit": 1}}}

    assert upgrade_node(node, 2) is node


def test_a_version_nothing_reaches_is_refused_rather_than_guessed_at():
    with pytest.raises(NoUpgradePath, match="no upgrade from about_content@1"):
        upgrade_node({"type": "about_content@1", "settings": {}}, 2)


class TestDocument:
    def test_a_child_is_upgraded_inside_a_parent_that_is_not(self):
        data = {
            "sections": {
                "a": {
                    "type": "hero_carousel@1",
                    "settings": {},
                    "blocks": {
                        "b": {
                            "type": "hero_slide@1",
                            "settings": {"productId": 7},
                        }
                    },
                    "blockOrder": ["b"],
                }
            },
            "order": ["a"],
        }

        upgraded, moved = upgrade_document(
            data, catalogue(hero_carousel=1, hero_slide=2)
        )

        child = upgraded["sections"]["a"]["blocks"]["b"]
        assert child == {"type": "hero_slide@2", "settings": {"product": 7}}
        assert upgraded["sections"]["a"]["type"] == "hero_carousel@1"
        assert moved == {"hero_slide@1": 1}

    def test_a_child_is_upgraded_inside_a_parent_that_is_too(self):
        data = {
            "sections": {
                "a": {
                    "type": "offers_preview@1",
                    "settings": {},
                    "blocks": {
                        "b": {"type": "hero_slide@1", "settings": {}},
                    },
                }
            },
            "order": ["a"],
        }

        upgraded, moved = upgrade_document(
            data, catalogue(offers_preview=2, hero_slide=2)
        )

        section = upgraded["sections"]["a"]
        assert section["type"] == "offers_preview@2"
        assert section["blocks"]["b"]["type"] == "hero_slide@2"
        assert moved == {"offers_preview@1": 1, "hero_slide@1": 1}

    def test_the_document_it_was_given_is_not_touched(self):
        data = {
            "sections": {"a": {"type": "offers_preview@1", "settings": {}}},
            "order": ["a"],
        }

        upgrade_document(data, catalogue(offers_preview=2))

        assert data["sections"]["a"]["type"] == "offers_preview@1"

    def test_a_block_the_catalogue_does_not_list_stays_for_the_validation(self):
        data = {
            "sections": {"a": {"type": "gone@1", "settings": {}}},
            "order": ["a"],
        }

        upgraded, moved = upgrade_document(data, catalogue(offers_preview=2))

        assert upgraded == data
        assert not moved
