"""The full ISO 3166-1 seed — replaces the CY-only seed this branch
started with, so any store can add a rate for any country without a
platform migration.

Mirrors ``test_seed_default_country.py``'s pattern for the sibling
migration. Every test clears ``Country`` first (deleting any
``ShippingRate`` first — its FK to ``country.Country`` is ``PROTECT``)
since the migration's ``sort_order``/uniqueness logic depends on
exactly which rows already exist.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from country.models import Country
from shipping.models import ShippingRate

MIGRATION = "country.migrations.0012_seed_iso_countries"


class _SchemaEditor:
    class connection:
        alias = "default"


@pytest.fixture
def seed_module():
    return importlib.import_module(MIGRATION)


@pytest.fixture
def seed(seed_module):
    def _run():
        from django.apps import apps

        seed_module.seed_iso_countries(apps, _SchemaEditor)

    return _run


def _reset():
    ShippingRate.objects.all().delete()
    Country.objects.all().delete()


@pytest.mark.django_db
class TestFreshEnvironmentSeeding:
    def test_seeds_every_iso_country(self, seed, seed_module):
        _reset()
        seed()
        assert Country.objects.count() == len(seed_module.ISO_COUNTRIES)

    def test_seeds_cyprus_with_correct_iso_data(self, seed):
        _reset()
        seed()

        cy = Country.objects.get(alpha_2="CY")
        assert cy.alpha_3 == "CYP"
        assert cy.iso_cc == 196
        assert cy.phone_code == 357
        assert cy.postal_code_pattern == r"\d{4}"
        assert cy.postal_code_example == "2008"
        cy.set_current_language("el")
        assert cy.name == "Κύπρος"
        cy.set_current_language("en")
        assert cy.name == "Cyprus"
        cy.set_current_language("de")
        assert cy.name == "Zypern"

    def test_a_country_absent_from_the_postal_table_stays_blank(self, seed):
        """North Korea (KP) is in the ISO seed but not in
        ``0011_country_postal_code_format``'s ``GOOGLE_POSTAL_FORMATS``
        (the Google service 404'd for it) — the blank model default
        must survive, not an empty-string lookup crash."""
        _reset()
        seed()

        kp = Country.objects.get(alpha_2="KP")
        assert kp.postal_code_pattern == ""
        assert kp.postal_code_example == ""

    def test_corrects_the_wrong_gr_iso_cc_from_0010(self, seed):
        _reset()
        Country.objects.create(
            alpha_2="GR", alpha_3="GRC", iso_cc=297, sort_order=0
        )

        seed()

        assert Country.objects.get(alpha_2="GR").iso_cc == 300

    def test_is_idempotent(self, seed):
        _reset()
        seed()
        first_count = Country.objects.count()

        seed()
        seed()

        assert Country.objects.count() == first_count
        assert Country.objects.filter(alpha_2="CY").count() == 1


@pytest.mark.django_db
class TestExistingEnvironmentIsUntouched:
    def test_does_not_rewrite_an_operator_customised_row(self, seed):
        _reset()
        custom = Country.objects.create(
            alpha_2="CY",
            alpha_3="CYP",
            iso_cc=999,
            phone_code=357,
            sort_order=42,
        )

        seed()

        custom.refresh_from_db()
        assert custom.iso_cc == 999, (
            "seeding overwrote an operator-customised country row — "
            "use get_or_create, never update_or_create"
        )
        assert Country.objects.filter(alpha_2="CY").count() == 1

    def test_leaves_an_operator_set_gr_iso_cc_alone(self, seed):
        """297 is corrected because it is 0010's own frozen (wrong)
        seed value — any OTHER value is an operator's call, not this
        migration's to override."""
        _reset()
        Country.objects.create(
            alpha_2="GR", alpha_3="GRC", iso_cc=123, sort_order=0
        )

        seed()

        assert Country.objects.get(alpha_2="GR").iso_cc == 123

    def test_an_already_corrected_gr_is_a_no_op(self, seed):
        _reset()
        Country.objects.create(
            alpha_2="GR", alpha_3="GRC", iso_cc=300, sort_order=0
        )

        seed()

        assert Country.objects.get(alpha_2="GR").iso_cc == 300


@pytest.mark.django_db
class TestSortOrderAndUniquenessCollisions:
    def test_appends_new_rows_after_the_existing_max_sort_order(
        self, seed, seed_module
    ):
        _reset()
        Country.objects.create(alpha_2="GR", alpha_3="GRC", sort_order=5)

        with patch.object(
            seed_module,
            "ISO_COUNTRIES",
            (("ZZ", "ZZZ", None, None, "Ζ", "Z-country", "Z"),),
        ):
            seed()

        assert Country.objects.get(alpha_2="ZZ").sort_order == 6

    def test_skips_a_row_whose_alpha_3_already_belongs_to_another_row(
        self, seed, seed_module
    ):
        """``alpha_3`` has no nullable escape hatch — an unresolvable
        clash skips the whole row rather than raising and aborting
        every other country's seed."""
        _reset()
        Country.objects.create(alpha_2="QQ", alpha_3="ZZZ", sort_order=0)

        with patch.object(
            seed_module,
            "ISO_COUNTRIES",
            (("ZZ", "ZZZ", None, None, "Ζ", "Z-country", "Z"),),
        ):
            seed()

        assert not Country.objects.filter(alpha_2="ZZ").exists()

    def test_creates_without_iso_cc_when_it_collides(self, seed, seed_module):
        _reset()
        Country.objects.create(
            alpha_2="QQ", alpha_3="QQQ", iso_cc=999, sort_order=0
        )

        with patch.object(
            seed_module,
            "ISO_COUNTRIES",
            (("ZZ", "ZZZ", 999, None, "Ζ", "Z-country", "Z"),),
        ):
            seed()

        assert Country.objects.get(alpha_2="ZZ").iso_cc is None

    def test_creates_el_en_de_translations_for_a_new_row(
        self, seed, seed_module
    ):
        _reset()

        with patch.object(
            seed_module,
            "ISO_COUNTRIES",
            (("ZZ", "ZZZ", None, None, "Ζήτα", "Zeta", "Zett"),),
        ):
            seed()

        zz = Country.objects.get(alpha_2="ZZ")
        for lang, expected in (("el", "Ζήτα"), ("en", "Zeta"), ("de", "Zett")):
            zz.set_current_language(lang)
            assert zz.name == expected
