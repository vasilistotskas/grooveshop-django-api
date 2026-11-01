"""The default documents a new store starts with are valid, today.

``builder/seeds/*.json`` are the storefront's, seeded without validation
(``builder.seeding``); this is where they are validated, against the
snapshot of the storefront's block catalogue that is pinned beside them
(``tests/fixtures/builder/catalogue.json``). Both are refreshed together, so
a block that changes under a seed fails here, not on a new store.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from builder import seeding
from builder.models import DocumentKind, kind_of_key
from builder.storefront import parse_catalogue
from builder.validation import validate_document

CATALOGUE = parse_catalogue(
    json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "fixtures"
            / "builder"
            / "catalogue.json"
        ).read_text(encoding="utf-8")
    )
)

SEEDS = seeding.load_seeds()


def test_there_is_a_seed_for_the_home_page():
    assert "template:home" in {seed.key for seed in SEEDS}


@pytest.mark.parametrize("seed", SEEDS, ids=lambda seed: seed.key)
class TestEverySeed:
    def validated(self, seed: seeding.Seed):
        issues, validated = validate_document(
            key=seed.key,
            data=seed.data,
            i18n=seed.i18n,
            catalogue=CATALOGUE,
            feature_on=lambda name: True,
        )
        assert issues == []
        assert validated is not None
        return validated

    def test_it_validates_against_the_catalogue(self, seed):
        self.validated(seed)

    def test_validation_changes_nothing_in_it(self, seed):
        # What is stored is the file as it is: validation must not rewrite
        # it (sanitising, natural sizes), or the draft and the version
        # would not be the document the storefront shipped.
        validated = self.validated(seed)

        assert validated.data == seed.data
        assert validated.i18n == seed.i18n

    def test_what_seeding_derives_is_what_validation_derives(self, seed):
        validated = self.validated(seed)

        assert seeding.derive(seed) == validated

    def test_it_is_for_a_template_the_catalogue_names(self, seed):
        assert kind_of_key(seed.key) is DocumentKind.TEMPLATE
        assert seed.key.partition(":")[2] in CATALOGUE.templates
