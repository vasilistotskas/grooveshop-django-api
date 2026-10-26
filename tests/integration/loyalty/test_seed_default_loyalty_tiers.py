"""A fresh tenant's loyalty program must have tiers; an existing one must not change.

``LoyaltyTierManager.get_for_level`` requires a row with
``required_level <= level`` to return anything, so a tenant with zero
tiers gives every customer (including a brand-new level-1 shopper)
``tier=None``. ``loyalty/migrations/0005_seed_default_loyalty_tiers``
closes that gap.

The dangerous half is the other direction: a store that already has
tiers owns its ladder, so the seeder adds nothing to it — not even the
default levels it lacks, which once put a second Gold and Platinum
beside a store's own.
"""

from __future__ import annotations

import importlib

import pytest

from loyalty.models.tier import LoyaltyTier, LoyaltyTierTranslation

MIGRATION = "loyalty.migrations.0005_seed_default_loyalty_tiers"


@pytest.fixture
def seed():
    module = importlib.import_module(MIGRATION)

    class _SchemaEditor:
        class connection:
            alias = "default"

    def _run():
        from django.apps import apps

        module.seed_loyalty_tiers(apps, _SchemaEditor)

    LoyaltyTier.objects.all().delete()

    return _run


@pytest.mark.django_db
class TestFreshTenantSeeding:
    def test_seeds_four_tiers(self, seed):
        seed()

        levels = set(
            LoyaltyTier.objects.values_list("required_level", flat=True)
        )
        assert levels == {1, 5, 15, 30}

    def test_bronze_covers_a_brand_new_level_one_user(self, seed):
        """Every account starts at level 1 — without a required_level=1
        row, a fresh tenant's own new customers would get no tier."""
        seed()

        tier = LoyaltyTier.objects.get_for_level(1)
        assert tier is not None
        assert tier.required_level == 1

    def test_multipliers_increase_with_tier(self, seed):
        seed()

        multipliers = list(
            LoyaltyTier.objects.order_by("required_level").values_list(
                "points_multiplier", flat=True
            )
        )
        assert multipliers == sorted(multipliers)
        assert len(set(multipliers)) == 4

    def test_translations_are_seeded_for_every_active_locale(self, seed):
        """LoyaltyTier is customer-facing (serialized directly via
        LoyaltyTierSerializer), unlike PayWay whose name the storefront
        resolves through its own i18n keys instead of the translation
        table — so every active locale needs a real name."""
        seed()

        for tier in LoyaltyTier.objects.all():
            for language_code in ("el", "en"):
                translation = LoyaltyTierTranslation.objects.get(
                    master=tier, language_code=language_code
                )
                assert translation.name, (
                    f"tier {tier.required_level} has no {language_code} name"
                )

    def test_is_idempotent(self, seed):
        seed()
        seed()
        seed()

        assert LoyaltyTier.objects.count() == 4
        assert LoyaltyTierTranslation.objects.count() == 12


@pytest.mark.django_db
class TestExistingTenantIsUntouched:
    def test_does_not_rewrite_a_merchant_customised_tier(self, seed):
        custom = LoyaltyTier.objects.create(
            required_level=1,
            points_multiplier="3.00",
            sort_order=0,
        )
        LoyaltyTierTranslation.objects.create(
            master=custom, language_code="el", name="VIP", description=""
        )

        seed()

        custom.refresh_from_db()
        assert str(custom.points_multiplier) == "3.00"
        assert LoyaltyTier.objects.count() == 1

    def test_adds_no_default_level_beside_a_store_ladder(self, seed):
        for level, sort_order in ((1, 0), (10, 1), (20, 2)):
            LoyaltyTier.objects.create(
                required_level=level,
                points_multiplier="1.00",
                sort_order=sort_order,
            )

        seed()

        levels = set(
            LoyaltyTier.objects.values_list("required_level", flat=True)
        )
        assert levels == {1, 10, 20}
