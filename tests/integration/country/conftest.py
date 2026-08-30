"""Isolate country integration tests from the seeded default country row.

``country/migrations/0010_seed_default_country`` creates one row (GR)
so a fresh environment's country table is not empty. That row is part
of the migrated test database, so a suite here that builds its own
``alpha_2="GR"`` fixture via ``CountryFactory`` (``django_get_or_create
= ("alpha_2",)``) would silently get the SEEDED row back instead of a
freshly created one, with whatever field values (``iso_cc``, no
``image_flag``, …) the seed happens to carry — not the ones the test
passed in.

Cleared ONCE per worker session, not per test: ``CountryViewSetTestCase``
(``tests/integration/country/test_view.py``) uses ``setUpTestData``,
which runs once per class and is shared across that class's test
methods via Django's savepoint rollback. A per-test delete would run
during pytest's fixture-setup phase — BEFORE ``TestCase`` creates that
per-test savepoint — so it would land outside it, permanently removing
the class's shared fixture after the first test method. Clearing once,
before anything in this directory runs, avoids that entirely: every
test that needs its own ``alpha_2="GR"`` row creates it fresh, inside
its own transaction, like any other fixture.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _clear_seeded_default_country(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        from country.models import Country

        Country.objects.filter(alpha_2="GR").delete()
