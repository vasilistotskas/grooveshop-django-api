"""Isolate vat integration tests from the seeded default VAT rates.

See ``tests/unit/vat/conftest.py`` for the rationale. The seeder's own
suite (``test_seed_default_vat_rates.py``) clears and re-runs the seed
itself, so this fixture does not interfere with it.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_seeded_vat_rates(db):
    from vat.models import Vat

    Vat.objects.all().delete()
