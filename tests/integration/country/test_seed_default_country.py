"""A fresh environment needs a country to attach data to; an existing
one must not have its data rewritten.

``country`` is SHARED_APPS-only (public schema) and had no data
migration, so a freshly provisioned environment started with an empty
table — no country to attach an address, a shipping zone, or a region
to. Mirrors ``vat``'s/``pay_way``'s/``loyalty``'s seed migrations,
which close the same kind of gap for their own tables.
"""

from __future__ import annotations

import importlib

import pytest

from country.models import Country

MIGRATION = "country.migrations.0010_seed_default_country"


@pytest.fixture
def seed():
    module = importlib.import_module(MIGRATION)

    class _SchemaEditor:
        class connection:
            alias = "default"

    def _run():
        from django.apps import apps

        module.seed_default_country(apps, _SchemaEditor)

    return _run


@pytest.mark.django_db
class TestFreshEnvironmentSeeding:
    def test_seeds_greece(self, seed):
        Country.objects.all().delete()

        seed()

        country = Country.objects.get(alpha_2="GR")
        assert country.alpha_3 == "GRC"
        assert country.iso_cc == 297
        assert country.phone_code == 30
        assert country.sort_order == 0

    def test_seeds_the_greek_translation(self, seed):
        Country.objects.all().delete()

        seed()

        country = Country.objects.get(alpha_2="GR")
        country.set_current_language("el")
        assert country.name == "Ελλάδα"

    def test_is_idempotent(self, seed):
        Country.objects.all().delete()

        seed()
        seed()
        seed()

        assert Country.objects.filter(alpha_2="GR").count() == 1
        country = Country.objects.get(alpha_2="GR")
        assert country.translations.filter(language_code="el").count() == 1


@pytest.mark.django_db
class TestExistingEnvironmentIsUntouched:
    def test_does_not_rewrite_an_operator_customised_row(self, seed):
        Country.objects.all().delete()
        custom = Country.objects.create(
            alpha_2="GR",
            alpha_3="GRC",
            iso_cc=999,
            phone_code=30,
            sort_order=5,
        )

        seed()

        custom.refresh_from_db()
        assert custom.iso_cc == 999, (
            "seeding overwrote an operator-customised country row — "
            "use get_or_create, never update_or_create"
        )
        assert Country.objects.filter(alpha_2="GR").count() == 1
