"""``0011_country_postal_code_format`` seeds Google's postcode formats.

The seed fills only rows whose pattern is still blank: an operator who
already set a format keeps it, the same non-destructive rule as the
``0010`` Greece seed.
"""

from __future__ import annotations

import importlib

import pytest

from country.factories import CountryFactory
from country.models import Country

MIGRATION = "country.migrations.0011_country_postal_code_format"


@pytest.fixture
def seed():
    module = importlib.import_module(MIGRATION)

    class _SchemaEditor:
        class connection:
            alias = "default"

    def _run():
        from django.apps import apps

        module.seed_postal_code_formats(apps, _SchemaEditor)

    return _run


@pytest.mark.django_db
class TestPostalCodeFormatSeed:
    def test_greece_gets_the_google_format(self, seed):
        greece = CountryFactory(alpha_2="GR", alpha_3="GRC")
        Country.objects.filter(pk=greece.pk).update(
            postal_code_pattern="", postal_code_example=""
        )

        seed()

        greece.refresh_from_db()
        assert greece.postal_code_pattern == r"\d{3} ?\d{2}"
        assert greece.postal_code_example == "151 24"

    def test_fills_a_blank_row(self, seed):
        cyprus = CountryFactory(alpha_2="CY", alpha_3="CYP")
        Country.objects.filter(pk=cyprus.pk).update(
            postal_code_pattern="", postal_code_example=""
        )

        seed()

        cyprus.refresh_from_db()
        assert cyprus.postal_code_pattern == r"\d{4}"
        assert cyprus.postal_code_example == "2008"

    def test_keeps_an_operator_set_format(self, seed):
        CountryFactory(alpha_2="GR", alpha_3="GRC")
        Country.objects.filter(alpha_2="GR").update(
            postal_code_pattern=r"[1-8]\d{4}", postal_code_example="70300"
        )

        seed()

        greece = Country.objects.get(alpha_2="GR")
        assert greece.postal_code_pattern == r"[1-8]\d{4}"
        assert greece.postal_code_example == "70300"

    def test_leaves_a_country_google_does_not_list_blank(self, seed):
        country = CountryFactory(alpha_2="ZZ", alpha_3="ZZZ")

        seed()

        country.refresh_from_db()
        assert country.postal_code_pattern == ""
