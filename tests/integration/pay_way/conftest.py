"""Isolate pay_way tests from the seeded default payment methods.

``pay_way/migrations/0019_seed_default_pay_ways`` creates three rows
(cash on delivery, Viva, Stripe) so a freshly provisioned tenant can
take an order. Those rows are part of the migrated test database, so
every test in this package that counts rows, asserts ordering, or
relies on ``SortableModel`` starting at ``sort_order=0`` would be
reading the seed data alongside its own fixtures.

Clearing them per-test restores the empty-table premise these suites
were written against. The delete happens inside the test's transaction,
so it is rolled back like any other test write — the seed itself is
untouched.

Tests that specifically cover the seeder live in
``test_seed_default_pay_ways.py`` and re-run it themselves, so this
fixture does not get in their way.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_seeded_pay_ways(db):
    from pay_way.models import PayWay

    PayWay.objects.all().delete()
