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
    SchemaUpgrade,
    upgrade_document,
    upgrade_node,
    upgrade_translations,
)
from builder.published import localised
from builder.seeding import seed_catalogue
from builder.storefront import parse_catalogue

CASES: list[dict[str, Any]] = json.loads(
    (
        Path(__file__).parents[2]
        / "fixtures"
        / "builder"
        / "upgrade-cases.json"
    ).read_text(encoding="utf-8")
)


def catalogue(*, translatable: tuple[str, ...] = (), **versions: int):
    return parse_catalogue(
        {
            "hash": "h",
            "blocks": [
                {
                    "type": name,
                    "version": version,
                    "schema": {"type": "object"},
                    "translatable": list(translatable),
                    "linkRefs": [],
                    "kinds": ["section"],
                }
                for name, version in versions.items()
            ],
        }
    )


def block_of(case: dict[str, Any]):
    return seed_catalogue().get(case["before"]["type"].partition("@")[0])


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_a_node_upgrades_as_the_storefront_upgrades_it(case):
    assert upgrade_node(case["before"], block_of(case)) == case["after"]


TRANSLATED = [case for case in CASES if "translations" in case]


@pytest.mark.parametrize("case", TRANSLATED, ids=lambda case: case["name"])
def test_translations_move_as_the_storefront_moves_them(case):
    moved = upgrade_translations(
        case["before"], block_of(case), case["translations"]
    )

    assert moved == case["translationsAfter"]


@pytest.mark.parametrize("case", TRANSLATED, ids=lambda case: case["name"])
def test_a_language_reads_what_it_read_across_an_upgrade(case):
    """Before: the overlay merged, then upgraded at render. After: the stored
    node upgraded, then the moved overlay merged."""
    definition = block_of(case)
    node, translations = case["before"], case["translations"]
    moved = upgrade_translations(node, definition, translations)

    read_before = upgrade_node(
        {**node, "settings": localised(node["settings"], translations)},
        definition,
    )["settings"]
    read_after = localised(upgrade_node(node, definition)["settings"], moved)

    for pointer in definition.translatable:
        name = pointer.removeprefix("/")
        assert read_after.get(name) == read_before.get(name), pointer


def test_an_empty_translation_goes_as_the_editor_takes_it_out():
    node = {
        "type": "offers_preview@2",
        "settings": {"eyebrow": "Προσφορές", "offers": {"limit": 3}},
    }
    definition = seed_catalogue().get("offers_preview")

    assert upgrade_translations(node, definition, {"/eyebrow": ""}) == {}


def test_the_translations_of_a_node_already_current_stay_as_they_are():
    node = {"type": "testimonials@2", "settings": {"heading": "Λένε"}}
    definition = seed_catalogue().get("testimonials")
    translations = {"/heading": "They say", "/stale": "kept"}

    assert upgrade_translations(node, definition, translations) is translations


def test_every_upgrade_here_has_a_case():
    covered = {case["before"]["type"].partition("@")[0] for case in CASES}

    assert set(UPGRADES) <= covered


def test_a_node_at_its_current_version_is_left_alone():
    node = {"type": "offers_preview@2", "settings": {"offers": {"limit": 1}}}

    assert (
        upgrade_node(node, catalogue(offers_preview=2).get("offers_preview"))
        is node
    )


def test_a_version_nothing_reaches_is_refused_rather_than_guessed_at():
    with pytest.raises(NoUpgradePath, match="no upgrade from about_content@1"):
        upgrade_node(
            {"type": "about_content@1", "settings": {}},
            catalogue(about_content=2).get("about_content"),
        )


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

        upgraded, _, moved = upgrade_document(
            data, {}, catalogue(hero_carousel=1, hero_slide=2)
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

        upgraded, _, moved = upgrade_document(
            data, {}, catalogue(offers_preview=2, hero_slide=2)
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

        upgrade_document(data, {}, catalogue(offers_preview=2))

        assert data["sections"]["a"]["type"] == "offers_preview@1"

    def test_a_block_the_catalogue_does_not_list_stays_for_the_validation(self):
        data = {
            "sections": {"a": {"type": "gone@1", "settings": {}}},
            "order": ["a"],
        }

        upgraded, _, moved = upgrade_document(
            data, {}, catalogue(offers_preview=2)
        )

        assert upgraded == data
        assert not moved


class TestOverlay:
    @staticmethod
    def upgraded(i18n: dict[str, Any]) -> dict[str, Any]:
        data = {
            "sections": {
                "old": {
                    "type": "testimonials@1",
                    "settings": {"title": "Τίτλος"},
                    "blocks": {
                        "child": {
                            "type": "testimonials@1",
                            "settings": {"title": "Παιδί"},
                        }
                    },
                    "blockOrder": ["child"],
                },
                "new": {
                    "type": "testimonials@2",
                    "settings": {"heading": "Νέο"},
                },
            },
            "order": ["old", "new"],
        }
        block = catalogue(translatable=("/heading",), testimonials=2)
        return upgrade_document(data, i18n, block)[1]

    def test_each_language_moves_with_its_node_at_every_depth(self):
        assert self.upgraded(
            {
                "en": {"old": {"/title": "Title"}, "child": {"/title": "Kid"}},
                "de": {"old": {"/title": "Titel"}},
            }
        ) == {
            "en": {"old": {"/heading": "Title"}, "child": {"/heading": "Kid"}},
            "de": {"old": {"/heading": "Titel"}},
        }

    def test_what_is_not_a_moved_node_stays_as_it_was(self):
        i18n = {
            "en": {
                "new": {"/heading": "New", "/stale": "kept"},
                "@document": {"/seoTitle": "Home"},
                "gone": {"/title": "Gone"},
            }
        }

        assert self.upgraded(i18n) == i18n

    def test_a_node_and_a_language_left_with_nothing_go(self):
        assert self.upgraded({"en": {"old": {"/dropped": "x"}}}) == {}
        assert self.upgraded(
            {"en": {"old": {"/dropped": "x"}, "new": {"/heading": "New"}}}
        ) == {"en": {"new": {"/heading": "New"}}}

    def test_the_overlay_it_was_given_is_not_touched(self):
        i18n = {"en": {"old": {"/title": "Title"}, "new": {"/heading": "New"}}}

        result = self.upgraded(i18n)
        result["en"]["new"]["/heading"] = "Changed"

        assert i18n == {
            "en": {"old": {"/title": "Title"}, "new": {"/heading": "New"}}
        }


def icon_block(*names: str):
    """A block whose icon setting takes *names*, as the catalogue states it."""
    return parse_catalogue(
        {
            "hash": "h",
            "blocks": [
                {
                    "type": "nav_link",
                    "version": 2,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "icon": {"type": "string", "enum": list(names)},
                        },
                    },
                    "translatable": [],
                    "linkRefs": [],
                    "kinds": ["block"],
                }
            ],
        }
    ).get("nav_link")


class TestIconOntoTheList:
    """The icon settings took the storefront's list (``BUILDER_ICONS``): what
    a node saved before writes as is the list's entry, read off the catalogue
    the store is checked against, so the port keeps what the storefront keeps
    however the list grows."""

    def node(self, **settings):
        return {
            "type": "nav_link@1",
            "settings": {"label": "Βοήθεια", **settings},
        }

    @pytest.mark.parametrize(
        ("saved", "entry"),
        [
            ("i-lucide-shield", "i-lucide-shield"),
            ("i-lucide:shield", "i-lucide-shield"),
            ("i-fa6-solid:shield", "i-fa6-solid-shield"),
        ],
    )
    def test_a_saved_icon_is_written_as_its_entry(self, saved, entry):
        block = icon_block("i-lucide-shield", "i-fa6-solid-shield")

        upgraded = upgrade_node(self.node(icon=saved), block)

        assert upgraded == {
            "type": "nav_link@2",
            "settings": {"label": "Βοήθεια", "icon": entry},
        }

    def test_an_icon_the_list_does_not_hold_is_left_out(self):
        upgraded = upgrade_node(
            self.node(icon="i-lucide-star"), icon_block("i-lucide-shield")
        )

        assert upgraded["settings"] == {"label": "Βοήθεια"}

    def test_a_node_without_an_icon_is_only_renumbered(self):
        upgraded = upgrade_node(self.node(), icon_block("i-lucide-shield"))

        assert upgraded == {
            "type": "nav_link@2",
            "settings": {"label": "Βοήθεια"},
        }

    def test_an_icon_added_to_the_list_later_is_kept(self):
        block = icon_block("i-lucide-shield", "i-lucide-star")

        upgraded = upgrade_node(self.node(icon="i-lucide-star"), block)

        assert upgraded["settings"]["icon"] == "i-lucide-star"

    def test_a_catalogue_that_lists_no_icons_is_refused_rather_than_guessed_at(
        self,
    ):
        block = catalogue(nav_link=2).get("nav_link")

        with pytest.raises(NoUpgradePath, match="lists no icons"):
            upgrade_node(self.node(icon="i-lucide-shield"), block)

    @pytest.mark.parametrize(
        "name",
        [
            "blog_posts_grid",
            "feature_item",
            "footer_column",
            "nav_link",
            "timeline_entry",
            "trust_badge",
        ],
    )
    def test_every_block_with_an_icon_setting_takes_the_step(self, name):
        last = max(UPGRADES[name])

        assert isinstance(UPGRADES[name][last], SchemaUpgrade)
