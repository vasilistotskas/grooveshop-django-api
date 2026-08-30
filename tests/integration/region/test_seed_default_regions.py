"""A fresh environment needs the 12 Greek regions; an existing one
must not have its data rewritten.

Depends on ``country/migrations/0010_seed_default_country`` for its FK
target, so ``seed`` below runs both migrations to stay self-contained
regardless of what has already run against the shared test database.
"""

from __future__ import annotations

import importlib

import pytest

from country.models import Country
from region.models import Region

COUNTRY_MIGRATION = "country.migrations.0010_seed_default_country"
REGION_MIGRATION = "region.migrations.0009_seed_default_regions"

EXPECTED_ALPHAS = {
    "GR-14",
    "GR-17",
    "GR-6",
    "GR-8",
    "GR-13",
    "GR-10",
    "GR-15",
    "GR-9",
    "GR-5",
    "GR-4",
    "GR-7",
    "GR-12",
}


class _SchemaEditor:
    class connection:
        alias = "default"


@pytest.fixture
def seed():
    country_module = importlib.import_module(COUNTRY_MIGRATION)
    region_module = importlib.import_module(REGION_MIGRATION)

    def _run():
        from django.apps import apps

        country_module.seed_default_country(apps, _SchemaEditor)
        region_module.seed_default_regions(apps, _SchemaEditor)

    return _run


@pytest.fixture
def seed_regions_only():
    """Runs only the region migration, without pre-seeding the country."""
    region_module = importlib.import_module(REGION_MIGRATION)

    def _run():
        from django.apps import apps

        region_module.seed_default_regions(apps, _SchemaEditor)

    return _run


def _reset():
    Region.objects.all().delete()
    Country.objects.filter(alpha_2="GR").delete()


@pytest.mark.django_db
class TestFreshEnvironmentSeeding:
    def test_seeds_all_twelve_regions(self, seed):
        _reset()

        seed()

        alphas = set(
            Region.objects.filter(country_id="GR").values_list(
                "alpha", flat=True
            )
        )
        assert alphas == EXPECTED_ALPHAS

    def test_attica_is_sort_order_one(self, seed):
        """Attica (Athens) is the highest-traffic region — it must
        sort first, ahead of the other 11."""
        _reset()

        seed()

        attica = Region.objects.get(alpha="GR-14")
        assert attica.sort_order == 1
        assert attica.country_id == "GR"

    def test_seeds_the_greek_translation_for_every_region(self, seed):
        _reset()

        seed()

        for region in Region.objects.filter(country_id="GR"):
            region.set_current_language("el")
            assert region.name, f"region {region.alpha} has no el name"

    def test_is_idempotent(self, seed):
        _reset()

        seed()
        seed()
        seed()

        assert Region.objects.filter(country_id="GR").count() == 12

    def test_skips_gracefully_when_the_country_is_missing(
        self, seed_regions_only
    ):
        """If GR was deleted, there is nothing sensible to attach a
        region to — the seeder must not raise."""
        _reset()

        seed_regions_only()

        assert Region.objects.count() == 0


@pytest.mark.django_db
class TestExistingEnvironmentIsUntouched:
    def test_does_not_rewrite_an_operator_customised_region(self, seed):
        _reset()
        country = Country.objects.create(
            alpha_2="GR", alpha_3="GRC", phone_code=30
        )
        custom = Region.objects.create(
            alpha="GR-14", country=country, sort_order=99
        )

        seed()

        custom.refresh_from_db()
        assert custom.sort_order == 99, (
            "seeding overwrote an operator-customised region row — "
            "use get_or_create, never update_or_create"
        )
        assert Region.objects.filter(alpha="GR-14").count() == 1
