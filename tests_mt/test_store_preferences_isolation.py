"""``TenantConfig.preferences`` reads each store's own schema.

The resolve payload is built from the public schema (``Tenant`` is a
shared model) for a store whose settings live in its own schema, so
``effective_preferences`` must switch to that schema to read them. The
main suite has the public schema only and cannot tell the two apart.
"""

from __future__ import annotations

import pytest
from django_tenants.utils import schema_context

from tenant.features import effective_preferences

KEY = "MOBILE_BOTTOM_NAV_ENABLED"


def _set(enabled: bool) -> None:
    from extra_settings.models import Setting

    Setting.objects.update_or_create(
        name=KEY, defaults={"value_type": "bool", "value_bool": enabled}
    )


@pytest.fixture(autouse=True)
def _cleanup(mt_tenant, mt_tenant_b):
    yield
    from extra_settings.models import Setting

    for schema in (mt_tenant.schema_name, mt_tenant_b.schema_name, "public"):
        with schema_context(schema):
            Setting.objects.filter(name=KEY).delete()


@pytest.mark.django_db
def test_each_store_reads_its_own_preference_from_the_public_schema(
    mt_tenant, mt_tenant_b
):
    with schema_context(mt_tenant.schema_name):
        _set(False)
    with schema_context(mt_tenant_b.schema_name):
        _set(True)

    with schema_context("public"):
        _set(True)
        assert effective_preferences(mt_tenant)["mobile_bottom_nav"] is False, (
            "the store's preference was read from the public schema"
        )
        assert effective_preferences(mt_tenant_b)["mobile_bottom_nav"] is True
