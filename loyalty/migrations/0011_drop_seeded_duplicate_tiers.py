"""Remove the default tiers ``0005`` added beside a store's own ladder.

``0005_seed_default_loyalty_tiers`` get-or-created Bronze/Silver/Gold/
Platinum keyed on ``required_level``. A store that already had its own
ladder at other levels (webside: Gold at 10, Platinum at 20) therefore
got a second Gold at 15 and a second Platinum at 30, shown twice in the
admin and on the loyalty page.

A tier is removed only when a LOWER tier carries one of its names with
the same points multiplier, and it has no icon: the customer-visible
ladder is unchanged, since ``get_for_level`` resolves a level to the
highest tier at or below it, which is then the lower row of the same
name and rate. Accounts stamped with a removed tier move to that lower
row. ``sort_order`` is then re-sequenced along ``required_level``.
"""

from __future__ import annotations

from django.db import migrations


def drop_seeded_duplicate_tiers(apps, schema_editor):
    LoyaltyTier = apps.get_model("loyalty", "LoyaltyTier")
    UserAccount = apps.get_model("user", "UserAccount")
    db_alias = schema_editor.connection.alias
    tiers = LoyaltyTier.objects.using(db_alias)

    kept = []
    for tier in tiers.prefetch_related("translations").order_by(
        "required_level"
    ):
        names = {row.name for row in tier.translations.all()}
        keeper = next(
            (
                lower
                for lower, lower_names in kept
                if names & lower_names
                and lower.points_multiplier == tier.points_multiplier
            ),
            None,
        )
        if keeper is None or tier.icon:
            kept.append((tier, names))
            continue
        UserAccount.objects.using(db_alias).filter(
            loyalty_tier_id=tier.pk
        ).update(loyalty_tier_id=keeper.pk)
        tier.delete()

    for index, tier in enumerate(tiers.order_by("required_level")):
        if tier.sort_order != index:
            tier.sort_order = index
            tier.save(update_fields=["sort_order"])


class Migration(migrations.Migration):
    dependencies = [
        ("loyalty", "0010_remove_german_translations"),
        ("user", "0021_useraccount_loyalty_tier_useraccount_total_xp_and_more"),
    ]

    operations = [
        migrations.RunPython(
            drop_seeded_duplicate_tiers,
            migrations.RunPython.noop,
            elidable=True,
        ),
    ]
