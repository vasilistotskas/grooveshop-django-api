"""Seed convergence: a freshly-provisioned tenant schema must already
have the seed rows the app depends on (product write API, checkout,
loyalty display) — the regression this lane exists to catch is a seed
migration that silently stops running for NEW schemas (e.g. because it
was written against ``0002_seed_webside_tenant``'s public-only path by
mistake), which the main suite's routerless/middleware-less setup can
never observe.
"""

from __future__ import annotations

import pytest
from django_tenants.utils import schema_context


@pytest.mark.django_db
def test_fresh_tenant_schema_is_fully_migrated(mt_tenant):
    """Sanity precondition every other test in this lane relies on."""
    from django.db import connection

    with schema_context(mt_tenant.schema_name):
        assert connection.schema_name == mt_tenant.schema_name
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass(%s)",
                [f"{mt_tenant.schema_name}.vat_vat"],
            )
            assert cursor.fetchone()[0] is not None, (
                "vat_vat table missing from the tenant schema — it was "
                "not fully migrated"
            )


@pytest.mark.django_db
def test_fresh_tenant_schema_has_seeded_reference_data(mt_tenant):
    """The TENANT_APPS seed migrations (vat/pay_way/loyalty) must have
    already run for this schema — each closes a "brand new tenant
    can't do X" gap (product pricing, checkout, loyalty tiers)."""
    from loyalty.models.tier import LoyaltyTier
    from pay_way.models import PayWay
    from vat.models import Vat

    with schema_context(mt_tenant.schema_name):
        assert Vat.objects.exists(), (
            "no seeded Vat rows — vat/migrations/"
            "0007_seed_default_vat_rates did not run for this schema"
        )
        assert PayWay.objects.filter(
            provider_code="cash_on_delivery"
        ).exists(), (
            "no seeded cash_on_delivery PayWay — pay_way/migrations/"
            "0019_seed_default_pay_ways did not run for this schema"
        )
        assert LoyaltyTier.objects.filter(required_level=1).exists(), (
            "no seeded Bronze loyalty tier — loyalty/migrations/"
            "0005_seed_default_loyalty_tiers did not run for this schema"
        )
