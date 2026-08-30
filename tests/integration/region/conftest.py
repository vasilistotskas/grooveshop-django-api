"""Isolate region integration tests from the seeded default regions.

``region/migrations/0009_seed_default_regions`` creates 12 rows
attached to the seeded ``GR`` country, so a fresh environment's region
table is not empty. Those rows are part of the migrated test database;
a suite here that lists/paginates ``Region.objects.all()`` with no
filter (e.g. ``RegionFilterTestCase.test_empty_filter_values``) would
count the seed alongside its own fixtures.

Cleared ONCE per worker session rather than per test — see
``tests/integration/country/conftest.py`` for why: ``RegionFilterTestCase``
uses ``setUpTestData``, and a per-test delete would tear down that
class-scoped fixture between its test methods.

The seeded ``GR`` country itself is left alone here (only Region rows
are cleared) — no test in this directory depends on its absence, and
``country``'s own conftest already owns that decision for its own
directory.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _clear_seeded_default_regions(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        from region.models import Region

        Region.objects.filter(country_id="GR").delete()
