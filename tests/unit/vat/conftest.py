"""Isolate vat tests from the seeded default VAT rates.

``vat/migrations/0007_seed_default_vat_rates`` creates four rows (24,
13, 6, 0) so a freshly provisioned tenant can price products and use
the product write API. Those rows are part of the migrated test
database, so any suite here that counts ``Vat`` rows would be counting
the seed alongside its own fixtures.

Clearing them per-test restores the empty-table premise these suites
were written against. The delete runs inside the test's transaction and
is rolled back like any other test write — the seed itself is untouched.

Mirrors ``tests/{unit,integration}/pay_way/conftest.py``, which exists
for the same reason.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_seeded_vat_rates(db):
    from vat.models import Vat

    Vat.objects.all().delete()
