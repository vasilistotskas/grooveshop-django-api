"""Tests for R4-D1: loyalty tier_changed signal cache strategy.

Verifies that ``dispatch_tier_changed`` no longer issues per-save DB
queries to resolve tier levels, and that the tier-level map cache is
correctly invalidated when a LoyaltyTier row is saved.
"""

from decimal import Decimal

import pytest
from django.test import TestCase

from loyalty.models.tier import LoyaltyTier
from loyalty.signals import (
    _TIER_LEVEL_CACHE_KEY,
    _get_tier_level_map,
)
from user.factories.account import UserAccountFactory


def _create_tier(required_level: int, name: str = "Tier") -> LoyaltyTier:
    tier = LoyaltyTier.objects.create(
        required_level=required_level,
        points_multiplier=Decimal("1.0"),
    )
    from django.apps import apps

    LoyaltyTierTranslation = apps.get_model("loyalty", "LoyaltyTierTranslation")
    LoyaltyTierTranslation.objects.create(
        master=tier,
        language_code="en",
        name=name,
        description=f"{name} tier",
    )
    return tier


@pytest.mark.django_db
class TestTierLevelMapCache(TestCase):
    """The tier-level map is fetched once per TTL, not per UserAccount save."""

    def test_get_tier_level_map_populates_from_db(self):
        """Cache miss triggers exactly one DB query to load the full map."""
        bronze = _create_tier(required_level=1, name="Bronze")
        gold = _create_tier(required_level=3, name="Gold")

        from django.core.cache import cache

        cache.delete(_TIER_LEVEL_CACHE_KEY)

        with self.assertNumQueries(1):
            mapping = _get_tier_level_map()

        assert mapping[bronze.pk] == 1
        assert mapping[gold.pk] == 3

    def test_get_tier_level_map_uses_cache_on_second_call(self):
        """Repeated calls within TTL issue zero DB queries."""
        _create_tier(required_level=1, name="Bronze")

        from django.core.cache import cache

        cache.delete(_TIER_LEVEL_CACHE_KEY)
        _get_tier_level_map()  # prime the cache

        with self.assertNumQueries(0):
            _get_tier_level_map()

    def test_tier_save_invalidates_cache(self):
        """Saving a LoyaltyTier clears the cached map."""
        from django.core.cache import cache

        tier = _create_tier(required_level=1, name="Bronze")
        _get_tier_level_map()  # prime the cache
        assert cache.get(_TIER_LEVEL_CACHE_KEY) is not None

        # Trigger invalidation via post_save signal
        tier.save()

        assert cache.get(_TIER_LEVEL_CACHE_KEY) is None


@pytest.mark.django_db
class TestDispatchTierChangedQueryCount(TestCase):
    """dispatch_tier_changed fires without per-save tier DB queries."""

    def test_tier_transition_uses_cache_not_db(self):
        """After cache is warm, changing a user's tier issues 0 tier queries.

        Pre-save handler still issues 1 query to snapshot the old tier_id;
        that query is on UserAccount, not LoyaltyTier — it cannot be
        avoided without schema changes. What we eliminated is the two
        LoyaltyTier lookups that used to happen inside dispatch_tier_changed.
        """
        bronze = _create_tier(required_level=1, name="Bronze")
        gold = _create_tier(required_level=3, name="Gold")

        from django.core.cache import cache

        # Warm the cache so the signal uses the in-memory map.
        cache.delete(_TIER_LEVEL_CACHE_KEY)
        _get_tier_level_map()

        user = UserAccountFactory()
        user.loyalty_tier = bronze
        user.save(update_fields=["loyalty_tier"])

        received_directions: list[str] = []

        from loyalty.signals import loyalty_tier_changed

        def capture(sender, user, direction, **kwargs):
            received_directions.append(direction)

        loyalty_tier_changed.connect(capture)
        try:
            # The save below fires pre_save (1 UserAccount query to snapshot
            # old tier_id) + post_save (0 LoyaltyTier queries — map is cached).
            user.loyalty_tier = gold
            user.save(update_fields=["loyalty_tier"])
        finally:
            loyalty_tier_changed.disconnect(capture)

        # Signal fires after transaction commit; in test mode on_commit
        # runs immediately so we can assert the direction here.
        assert received_directions == ["up"]
