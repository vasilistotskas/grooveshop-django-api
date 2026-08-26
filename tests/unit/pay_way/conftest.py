"""Isolate pay_way unit tests from the seeded default payment methods.

See ``tests/integration/pay_way/conftest.py`` for the rationale: the
seeder in ``pay_way/migrations/0019_seed_default_pay_ways`` puts three
rows in the migrated test database, which otherwise show up in row
counts, ordering assertions, and ``SortableModel``'s auto-assigned
``sort_order``.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_seeded_pay_ways(db):
    from pay_way.models import PayWay

    PayWay.objects.all().delete()
