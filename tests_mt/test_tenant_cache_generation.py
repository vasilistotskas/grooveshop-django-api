"""The tenant caches are invalidated by generation, across schemas.

``Tenant.cache_generation`` lives in the PUBLIC schema, and is part of
every key the resolve payload and domain set are read under
(``tenant/cache.py``). Pay-ways and the agent settings live in the
TENANT schema but are folded into that payload, so their receivers bump
the public row from inside the tenant schema's transaction, relying on
the tenant ``search_path`` ending in ``public``. The main suite has no
tenant schemas, so this lane is the only place that is real — and it
runs on the real Redis-backed ``CustomCache``.
"""

from __future__ import annotations

import pytest
from django.db import transaction
from django_tenants.test.client import TenantClient
from django_tenants.utils import schema_context

from tenant.models import Tenant

pytestmark = pytest.mark.django_db


def _generation(tenant) -> int:
    return Tenant.objects.values_list("cache_generation", flat=True).get(
        pk=tenant.pk
    )


def _cash_on_delivery():
    from pay_way.enum.settlement import PaySettlement
    from pay_way.factories import PayWayFactory

    return PayWayFactory(
        active=True,
        settlement=PaySettlement.COURIER_CASH,
        provider_code="cash_on_delivery",
    )


def test_a_pay_way_write_in_the_tenant_schema_bumps_the_public_row(mt_tenant):
    before = _generation(mt_tenant)

    with schema_context(mt_tenant.schema_name):
        pay_way = _cash_on_delivery()
        pay_way.delete()

    assert _generation(mt_tenant) > before


def test_the_bump_rolls_back_with_the_write(mt_tenant):
    before = _generation(mt_tenant)

    with (
        schema_context(mt_tenant.schema_name),
        pytest.raises(RuntimeError),
        transaction.atomic(),
    ):
        _cash_on_delivery()
        raise RuntimeError

    assert _generation(mt_tenant) == before


def test_resolve_serves_a_committed_tenant_write_through_real_redis(
    mt_tenant,
):
    url = f"/api/v1/tenant/resolve?domain={mt_tenant.get_primary_domain()}"
    client = TenantClient(mt_tenant)
    original = mt_tenant.store_name
    assert client.get(url).status_code == 200

    tenant = Tenant.objects.get(pk=mt_tenant.pk)
    tenant.store_name = "MT renamed"
    tenant.save(update_fields=["store_name"])
    try:
        assert client.get(url).json()["storeName"] == "MT renamed"
    finally:
        tenant.store_name = original
        tenant.save(update_fields=["store_name"])
