"""B2B tables and the wholesale gate are schema-bound.

The main suite strips multi-tenancy, so two things are untestable
there: (1) ``b2b`` rows (groups, price lists) created in one tenant
schema must be invisible to another, and (2) ``B2BService.is_enabled``
must read the ACTIVE schema's ``B2B_WHOLESALE_ENABLED`` row combined
with THAT tenant's plan flag — never a neighbour's.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import transaction
from django.db.utils import ProgrammingError
from django_tenants.utils import schema_context

_SETTING_NAME = "B2B_WHOLESALE_ENABLED"


@pytest.mark.django_db
def test_b2b_rows_are_schema_local(mt_tenant):
    """``b2b`` is TENANT_APPS-only, so its tables must not exist in the
    public schema AT ALL — the query itself fails from public (the
    stronger guarantee, mirroring test_model_write_isolation.py)."""
    from b2b.models import CustomerGroup

    with schema_context(mt_tenant.schema_name):
        CustomerGroup.objects.create(
            name="mt-probe-wholesale", discount_percent=Decimal(10)
        )
        assert CustomerGroup.objects.filter(name="mt-probe-wholesale").exists()

    with schema_context("public"):
        # Savepoint so the expected error doesn't poison the outer
        # test transaction.
        with (
            pytest.raises(ProgrammingError, match="does not exist"),
            transaction.atomic(),
        ):
            CustomerGroup.objects.filter(name="mt-probe-wholesale").exists()


@pytest.mark.django_db
def test_wholesale_gate_reads_active_schema(mt_tenant):
    from extra_settings.models import Setting

    from b2b.services import B2BService

    mt_tenant.b2b_enabled = True
    mt_tenant.save(update_fields=["b2b_enabled"])

    with schema_context(mt_tenant.schema_name):
        Setting.objects.update_or_create(
            name=_SETTING_NAME,
            defaults={"value_type": "bool", "value_bool": True},
        )

    # Inside the tenant schema: plan flag AND setting hold.
    with schema_context(mt_tenant.schema_name):
        assert B2BService.is_enabled() is True

    # From public: tenant_plan_allows fails open on the public schema
    # but the public schema's OWN setting row (absent → default False)
    # must decide — the tenant's True row must not leak across.
    with schema_context("public"):
        Setting.objects.filter(name=_SETTING_NAME).delete()
        assert B2BService.is_enabled() is False
