"""The default documents a new store starts with are valid, today.

``builder/seeds/*.json`` are the storefront's, seeded without validation
(``builder.seeding``); this is where they are validated, against the
snapshots of the storefront's block catalogue that are pinned beside them
(``builder/catalogues/``), one per design. Every new store gets the seeds,
whatever its design, so each design's catalogue must take them. Both are
refreshed together, so a block that changes under a seed fails here, not
on a new store.
"""

from __future__ import annotations

import pytest

from builder import seeding
from builder.models import DocumentKind, kind_of_key
from builder.seeding import CATALOGUES, SEED_CATALOGUE
from builder.validation import validate_document
from tests.utils.real_catalogues import DESIGNS, catalogue

SEEDS = seeding.load_seeds()


def test_there_is_a_seed_for_the_home_page():
    assert "template:home" in {seed.key for seed in SEEDS}


def test_seeding_derives_against_the_default_designs_catalogue():
    # Every store whose schema no design names is served this one, and it
    # holds only what every design draws.
    assert "default" in DESIGNS
    assert SEED_CATALOGUE == CATALOGUES / "default.json"


@pytest.mark.parametrize("design", DESIGNS)
@pytest.mark.parametrize("seed", SEEDS, ids=lambda seed: seed.key)
class TestEverySeedOnEveryDesign:
    def validated(self, seed: seeding.Seed, design: str):
        issues, validated = validate_document(
            key=seed.key,
            data=seed.data,
            i18n=seed.i18n,
            catalogue=catalogue(design),
            feature_on=lambda name: True,
        )
        assert issues == []
        assert validated is not None
        return validated

    def test_it_validates_against_the_catalogue(self, seed, design):
        self.validated(seed, design)

    def test_validation_changes_nothing_in_it(self, seed, design):
        # What is stored is the file as it is: validation must not rewrite
        # it (sanitising, natural sizes), or the draft and the version
        # would not be the document the storefront shipped.
        validated = self.validated(seed, design)

        assert validated.data == seed.data
        assert validated.i18n == seed.i18n

    def test_what_seeding_derives_is_what_validation_derives(
        self, seed, design
    ):
        # Seeding derives against one catalogue for every store: what it
        # stores must be what each design's catalogue derives.
        validated = self.validated(seed, design)

        assert seeding.derive(seed) == validated

    def test_it_is_for_a_template_the_catalogue_names(self, seed, design):
        assert kind_of_key(seed.key) is DocumentKind.TEMPLATE
        assert seed.key.partition(":")[2] in catalogue(design).templates
