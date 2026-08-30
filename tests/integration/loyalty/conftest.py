"""Isolate loyalty integration tests from the seeded default tiers.

See ``tests/unit/test_loyalty/conftest.py`` for the rationale.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_seeded_loyalty_tiers(db):
    from loyalty.models.tier import LoyaltyTier

    LoyaltyTier.objects.all().delete()
