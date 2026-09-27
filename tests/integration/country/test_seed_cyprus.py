"""Cyprus is BoxNow-lockers-only for webside — ops data, not code — but
the country/region rows themselves are global reference data every
tenant needs before a store can even add a Cyprus rate.

Mirrors ``test_seed_default_country.py``'s pattern for the sibling
``0012_seed_cyprus`` migration. Every test clears ``Country`` first —
the ``_reseed_countries`` autouse fixture (``tests/conftest.py``)
already seeded GR by the time any test body runs, and this migration's
``sort_order`` depends on exactly which rows exist.
"""

from __future__ import annotations

import importlib

import pytest

from country.models import Country
from shipping.models import ShippingRate

MIGRATION = "country.migrations.0012_seed_cyprus"


@pytest.fixture
def seed():
    module = importlib.import_module(MIGRATION)

    class _SchemaEditor:
        class connection:
            alias = "default"

    def _run():
        from django.apps import apps

        module.seed_cyprus(apps, _SchemaEditor)

    return _run


@pytest.mark.django_db
class TestFreshEnvironmentSeeding:
    def test_seeds_cyprus(self, seed):
        ShippingRate.objects.all().delete()
        Country.objects.all().delete()

        seed()

        country = Country.objects.get(alpha_2="CY")
        assert country.alpha_3 == "CYP"
        assert country.iso_cc == 196
        assert country.phone_code == 357
        assert country.postal_code_pattern == r"\d{4}"
        assert country.postal_code_example == "2008"

    def test_sort_order_places_cyprus_after_every_existing_row(self, seed):
        ShippingRate.objects.all().delete()
        Country.objects.all().delete()
        Country.objects.create(alpha_2="GR", alpha_3="GRC", sort_order=0)
        Country.objects.create(alpha_2="DE", alpha_3="DEU", sort_order=5)

        seed()

        country = Country.objects.get(alpha_2="CY")
        assert country.sort_order == 6

    def test_seeds_greek_and_english_translations(self, seed):
        ShippingRate.objects.all().delete()
        Country.objects.all().delete()

        seed()

        country = Country.objects.get(alpha_2="CY")
        country.set_current_language("el")
        assert country.name == "Κύπρος"
        country.set_current_language("en")
        assert country.name == "Cyprus"

    def test_is_idempotent(self, seed):
        ShippingRate.objects.all().delete()
        Country.objects.all().delete()

        seed()
        seed()
        seed()

        assert Country.objects.filter(alpha_2="CY").count() == 1
        country = Country.objects.get(alpha_2="CY")
        assert country.translations.count() == 2


@pytest.mark.django_db
class TestExistingEnvironmentIsUntouched:
    def test_does_not_rewrite_an_operator_customised_row(self, seed):
        ShippingRate.objects.all().delete()
        Country.objects.all().delete()
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
