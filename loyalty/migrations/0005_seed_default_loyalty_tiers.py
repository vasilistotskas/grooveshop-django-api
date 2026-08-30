"""Seed default loyalty tiers so a fresh tenant's loyalty program works.

``loyalty`` is TENANT_APPS-only and had no data migration, so every new
tenant starts with an empty ``LoyaltyTier`` table (mirrors the ``vat``/
``pay_way`` gaps fixed in their own seed migrations). Consequence:
``LoyaltyTierManager.get_for_level`` requires a row with
``required_level <= level`` to return anything — with zero rows, EVERY
customer (including a brand-new level-1 shopper) gets ``tier=None`` from
``LoyaltyService.get_user_tier``/``get_user_summary``, so the loyalty
page has nothing to show and points multipliers never apply.

Seeded tiers mirror the four-tier shape already used as example data in
``loyalty/factories/tier.py`` (Bronze/Silver/Gold/Platinum). Bronze's
``required_level=1`` is load-bearing: every account starts at level 1
(``LoyaltyService.get_user_level``), so a fresh tenant would otherwise
have no tier covering its own new users even after this migration.

IDEMPOTENT AND NON-DESTRUCTIVE. ``get_or_create`` keyed on
``required_level`` (unique on the model), never ``update_or_create``:
a live tenant may already have merchant-customised tiers (renamed,
re-thresholded, or re-priced multipliers) and rewriting them would
silently change what customers are told they need to reach the next
tier. This migration only ever ADDS missing rows.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import migrations

# (required_level, points_multiplier, sort_order, name)
DEFAULT_LOYALTY_TIERS = [
    (1, Decimal("1.00"), 0, "Bronze"),
    (5, Decimal("1.25"), 1, "Silver"),
    (15, Decimal("1.50"), 2, "Gold"),
    (30, Decimal("2.00"), 3, "Platinum"),
]

# All three active storefront locales — LoyaltyTier is a customer-facing
# TranslatableModel (serialized directly via LoyaltyTierSerializer),
# unlike PayWay whose name the storefront resolves through its own
# i18n keys instead of the translation table.
SEED_TRANSLATIONS = {
    "el": {
        "Bronze": ("Χάλκινο", ""),
        "Silver": ("Ασημένιο", ""),
        "Gold": ("Χρυσό", ""),
        "Platinum": ("Πλατινένιο", ""),
    },
    "en": {
        "Bronze": ("Bronze", ""),
        "Silver": ("Silver", ""),
        "Gold": ("Gold", ""),
        "Platinum": ("Platinum", ""),
    },
    "de": {
        "Bronze": ("Bronze", ""),
        "Silver": ("Silber", ""),
        "Gold": ("Gold", ""),
        "Platinum": ("Platin", ""),
    },
}


def seed_loyalty_tiers(apps, schema_editor):
    LoyaltyTier = apps.get_model("loyalty", "LoyaltyTier")
    LoyaltyTierTranslation = apps.get_model(
        "loyalty", "LoyaltyTierTranslation"
    )
    db_alias = schema_editor.connection.alias

    for required_level, multiplier, sort_order, key in DEFAULT_LOYALTY_TIERS:
        tier, created = LoyaltyTier.objects.using(db_alias).get_or_create(
            required_level=required_level,
            defaults={
                "points_multiplier": multiplier,
                "sort_order": sort_order,
            },
        )
        if not created:
            # A merchant already owns this row — leave every field alone.
            continue

        for language_code, names in SEED_TRANSLATIONS.items():
            name, description = names[key]
            LoyaltyTierTranslation.objects.using(db_alias).get_or_create(
                master=tier,
                language_code=language_code,
                defaults={"name": name, "description": description},
            )


class Migration(migrations.Migration):
    dependencies = [
        ("loyalty", "0004_alter_loyaltytier_sort_order"),
    ]

    operations = [
        migrations.RunPython(
            seed_loyalty_tiers,
            # Reverse is a no-op: these rows are indistinguishable from
            # merchant-created ones, and deleting a tier a user has
            # already reached would break required_level lookups for
            # that user's loyalty history.
            migrations.RunPython.noop,
            elidable=False,
        ),
    ]
