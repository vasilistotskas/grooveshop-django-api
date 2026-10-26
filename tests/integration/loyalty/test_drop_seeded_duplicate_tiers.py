"""``0011`` removes the default tiers ``0005`` put beside a store's ladder.

The shape found on webside: its own Gold at level 10 and Platinum at 20,
plus the seeded Gold at 15 and Platinum at 30. Only a higher, iconless
tier repeating a lower tier's name at the same multiplier goes, so the
ladder a customer sees is unchanged.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from loyalty.models import LoyaltyTier
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

_migration = importlib.import_module(
    "loyalty.migrations.0011_drop_seeded_duplicate_tiers"
)


def _run():
    _migration.drop_seeded_duplicate_tiers(
        MigrationExecutor(connection).loader.project_state().apps,
        SimpleNamespace(connection=connection),
    )


def _ladder(*rows):
    """An exact ladder of ``(required_level, el name, multiplier)``."""
    LoyaltyTier.objects.all().delete()
    created = []
    for sort_order, (required_level, name, multiplier) in enumerate(rows):
        tier = LoyaltyTier(
            required_level=required_level,
            points_multiplier=multiplier,
            sort_order=sort_order,
        )
        tier.set_current_language("el")
        tier.name = name
        tier.save()
        created.append(tier)
    return created


def _levels():
    return list(
        LoyaltyTier.objects.order_by("required_level").values_list(
            "required_level", "sort_order"
        )
    )


def test_the_seeded_gold_and_platinum_beside_a_store_ladder_go():
    _ladder(
        (1, "Χάλκινο", "1.00"),
        (5, "Ασημένιο", "1.25"),
        (10, "Χρυσό", "1.50"),
        (15, "Χρυσό", "1.50"),
        (20, "Πλατινένιο", "2.00"),
        (30, "Πλατινένιο", "2.00"),
        (50, "Διαμάντι", "3.00"),
    )

    _run()

    assert _levels() == [(1, 0), (5, 1), (10, 2), (20, 3), (50, 4)]


def test_a_distinctly_named_ladder_is_left_alone():
    _ladder(
        (1, "Χάλκινο", "1.00"),
        (5, "Ασημένιο", "1.25"),
        (15, "Χρυσό", "1.50"),
        (30, "Πλατινένιο", "2.00"),
    )

    _run()

    assert _levels() == [(1, 0), (5, 1), (15, 2), (30, 3)]


def test_a_duplicate_with_its_own_icon_is_a_ladder_step():
    _, higher = _ladder((10, "Χρυσό", "1.50"), (15, "Χρυσό", "1.50"))
    higher.icon = "uploads/loyalty/gold.png"
    higher.save(update_fields=["icon"])

    _run()

    assert LoyaltyTier.objects.count() == 2


def test_a_duplicate_name_at_another_rate_is_a_ladder_step():
    _ladder((10, "Χρυσό", "1.50"), (15, "Χρυσό", "1.75"))

    _run()

    assert LoyaltyTier.objects.count() == 2


def test_an_account_on_a_removed_tier_moves_to_the_lower_one():
    keeper, duplicate = _ladder((10, "Χρυσό", "1.50"), (15, "Χρυσό", "1.50"))
    account = UserAccountFactory()
    type(account).objects.filter(pk=account.pk).update(
        loyalty_tier_id=duplicate.pk
    )

    _run()

    account.refresh_from_db()
    assert account.loyalty_tier_id == keeper.pk
    assert not LoyaltyTier.objects.filter(pk=duplicate.pk).exists()
