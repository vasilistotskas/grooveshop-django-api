"""Isolate loyalty unit tests from the seeded default tiers.

``loyalty/migrations/0005_seed_default_loyalty_tiers`` creates four rows
(Bronze/Silver/Gold/Platinum at required_level 1/5/15/30) so a freshly
provisioned tenant's loyalty program has something to assign. Those
rows are part of the migrated test database, so any test in this
package that creates a tier at ``required_level=1`` (several do, via a
raw ``LoyaltyTier.objects.create`` — not ``get_or_create``) would
collide with the seeded Bronze row's unique ``required_level``
constraint.

Clearing them per-test restores the empty-table premise these suites
were written against. The delete happens inside the test's transaction,
so it is rolled back like any other test write — the seed migration
itself is untouched.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_seeded_loyalty_tiers(db):
    from loyalty.models.tier import LoyaltyTier

    LoyaltyTier.objects.all().delete()
