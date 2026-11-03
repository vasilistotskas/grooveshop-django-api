"""A theme whose colours miss a required contrast can be saved but not
released; the editor shows every rule, Django blocks only the errors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from builder import services
from builder.errors import DocumentInvalid, InvalidCatalogueError

pytestmark = pytest.mark.django_db

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "builder"
        / "contrast-cases.json"
    ).read_text(encoding="utf-8")
)
GOOD, PALE_LIGHT, DIM_DARK, YELLOW = (t["settings"] for t in FIXTURE["themes"])
THEME = "theme"


@pytest.fixture
def theme_catalogue(catalogue):
    catalogue.document_kinds["theme"] = {
        "schema": {"type": "object"},
        "translatable": [],
        "linkRefs": [],
        "contrast": FIXTURE["rules"],
    }
    return catalogue


def save(admin, settings_, revision=0):
    return services.save_draft(
        admin,
        key=THEME,
        draft={"sections": {}, "order": [], "settings": settings_},
        i18n={},
        expected_revision=revision,
    )


class TestSavingNeverRefusesForContrast:
    def test_a_pale_theme_is_saved(self, admin, theme_catalogue, features_on):
        assert save(admin, PALE_LIGHT).draft_revision == 1


class TestPublishing:
    def test_a_theme_that_keeps_every_contrast_is_released(
        self, admin, theme_catalogue, features_on
    ):
        save(admin, GOOD)

        assert services.publish(admin, keys=[THEME]).release.number >= 1

    def test_body_text_that_misses_its_contrast_blocks_with_the_rule(
        self, admin, theme_catalogue, features_on
    ):
        save(admin, PALE_LIGHT)

        with pytest.raises(DocumentInvalid) as caught:
            services.publish(admin, keys=[THEME])

        (issue,) = caught.value.issues
        assert issue.code == "insufficient_contrast"
        assert issue.document == THEME
        assert issue.path == "/colors/light/neutral/scale/700"
        assert issue.params == {
            "rule": "body-text-light",
            "ratio": "2.56",
            "min": "4.5",
        }

    def test_the_dark_scheme_is_held_to_its_own_rule(
        self, admin, theme_catalogue, features_on
    ):
        save(admin, DIM_DARK)

        with pytest.raises(DocumentInvalid) as caught:
            services.publish(admin, keys=[THEME])

        assert [i.params["rule"] for i in caught.value.issues] == [
            "body-text-dark"
        ]

    def test_a_warning_alone_does_not_block(
        self, admin, theme_catalogue, features_on
    ):
        save(admin, YELLOW)

        assert services.publish(admin, keys=[THEME]).release.number >= 1

    def test_the_check_reports_it_and_writes_nothing(
        self, admin, theme_catalogue, features_on
    ):
        save(admin, PALE_LIGHT)

        check = services.check_publish(admin, keys=[THEME])

        assert [(i.document, i.code) for i in check.problems] == [
            (THEME, "insufficient_contrast")
        ]


class TestAContradictoryCatalogue:
    def test_a_rule_that_points_at_no_colour_is_a_503_not_a_pass(
        self, admin, theme_catalogue, features_on
    ):
        theme_catalogue.document_kinds["theme"]["contrast"] = [
            {
                "id": "nowhere",
                "foreground": "/colors/nowhere",
                "background": "#ffffff",
                "min": 4.5,
                "severity": "error",
            }
        ]
        save(admin, GOOD)

        with pytest.raises(InvalidCatalogueError):
            services.publish(admin, keys=[THEME])
