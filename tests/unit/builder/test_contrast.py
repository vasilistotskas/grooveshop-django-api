"""WCAG 2.2 contrast and the theme rules, against the storefront's cases.

``tests/fixtures/builder/contrast-cases.json`` is copied and pinned
(``.github/scripts/sync_upstream_sources.py``); the storefront runs the same
file against ``shared/utils/contrast.ts``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from builder import contrast

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "builder"
        / "contrast-cases.json"
    ).read_text(encoding="utf-8")
)
RULES = contrast.parse_rules(FIXTURE["rules"])
#: A rule's name in both languages, as the catalogue gives it.
LABEL = {"el": "Κείμενο, φωτεινό", "en": "Text, light"}


@pytest.mark.parametrize(
    "case", FIXTURE["cases"], ids=[c["why"] for c in FIXTURE["cases"]]
)
def test_the_ratio_of_two_colours(case):
    got = contrast.ratio(case["foreground"], case["background"])

    assert contrast.format_ratio(got) == case["ratio"]


@pytest.mark.parametrize(
    "case", FIXTURE["boundaries"], ids=[c["why"] for c in FIXTURE["boundaries"]]
)
def test_the_unrounded_ratio_decides(case):
    got = contrast.ratio(case["foreground"], case["background"])

    assert (got >= case["min"]) is case["pass"]


@pytest.mark.parametrize(
    "theme", FIXTURE["themes"], ids=[t["why"] for t in FIXTURE["themes"]]
)
def test_the_rules_over_a_whole_theme(theme):
    outcomes = contrast.evaluate(RULES, theme["settings"])

    assert [
        {
            "id": o.rule.id,
            "ratio": contrast.format_ratio(o.ratio),
            "pass": o.passed,
            "path": o.rule.path,
        }
        for o in outcomes
    ] == theme["outcomes"]


class TestACatalogueThatContradictsItself:
    def test_a_pointer_that_holds_no_colour(self):
        rule = contrast.parse_rules(
            [
                {
                    "id": "x",
                    "foreground": "/colors/nowhere",
                    "background": "#ffffff",
                    "min": 4.5,
                    "severity": "error",
                    "label": LABEL,
                }
            ]
        )

        with pytest.raises(contrast.ContrastCatalogueError):
            contrast.evaluate(rule, FIXTURE["themes"][0]["settings"])

    @pytest.mark.parametrize(
        "rule",
        [
            {"id": "Bad", "foreground": "#000000", "background": "#ffffff"},
            {"id": "x", "foreground": "red", "background": "#ffffff"},
            {
                "id": "x",
                "foreground": "#000000",
                "background": "#ffffff",
                "min": 0,
            },
            {
                "id": "x",
                "foreground": "#000000",
                "background": "#ffffff",
                "min": 22,
            },
            {
                "id": "x",
                "foreground": "#000000",
                "background": "#ffffff",
                "severity": "fatal",
            },
        ],
    )
    def test_a_malformed_rule_is_refused_when_the_catalogue_is_read(self, rule):
        with pytest.raises(ValueError):
            contrast.parse_rules(
                [{"min": 4.5, "severity": "error", "label": LABEL, **rule}]
            )

    @pytest.mark.parametrize(
        "label",
        [
            None,
            {"el": "Κείμενο, φωτεινό"},
            {"el": "Κείμενο, φωτεινό", "en": "  "},
            {"el": 3, "en": "Text, light"},
        ],
        ids=["none", "a language missing", "a blank name", "not text"],
    )
    def test_a_rule_not_named_in_every_language_is_refused(self, label):
        rule = {
            "id": "x",
            "foreground": "#000000",
            "background": "#ffffff",
            "min": 4.5,
            "severity": "error",
            "label": label,
        }

        with pytest.raises((ValueError, KeyError, AttributeError)):
            contrast.parse_rules([rule])

    def test_two_rules_with_one_id(self):
        rule = {
            "id": "x",
            "foreground": "#000000",
            "background": "#ffffff",
            "min": 4.5,
            "severity": "error",
            "label": LABEL,
        }

        with pytest.raises(ValueError):
            contrast.parse_rules([rule, rule])


class TestARuleIsNamedByTheCatalogue:
    def test_its_name_in_every_language_and_nothing_the_editor_keeps(self):
        (rule,) = contrast.parse_rules(
            [
                {
                    "id": "body-text-light",
                    "foreground": "#000000",
                    "background": "#ffffff",
                    "min": 4.5,
                    "severity": "error",
                    "label": {**LABEL, "de": "Text, hell"},
                    "fix": {"el": "Αλλάζει.", "en": "Changes it."},
                }
            ]
        )

        assert rule.label == LABEL

    def test_every_rule_of_the_shared_cases_is_named(self):
        assert all(set(rule.label) == {"el", "en"} for rule in RULES)
