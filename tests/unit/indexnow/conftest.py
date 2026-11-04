from __future__ import annotations

from unittest.mock import patch

import pytest

from tenant.models import TenantDomain
from tests.utils.staff import store_tenant

STORE_HOST = "indexnow-store.example.com"


def setting_reader(**overrides):
    """A ``Setting.get`` that answers *overrides* and the declared default
    for every other name (the way ``store_setting`` calls it)."""

    def _get(name, default=None):
        return overrides.get(name, default)

    return _get


@pytest.fixture
def store(db, bind_tenant_and_schema):
    """A store on its own schema name, English and Greek, with a primary
    domain, bound to the connection."""
    tenant = store_tenant("indexnow_store", available_locales=["el", "en"])
    TenantDomain.objects.create(
        tenant=tenant, domain=STORE_HOST, is_primary=True
    )
    bind_tenant_and_schema(tenant)
    return tenant


@pytest.fixture
def indexnow_on(store):
    with patch(
        "extra_settings.models.Setting.get",
        side_effect=setting_reader(INDEXNOW_ENABLED=True),
    ):
        yield store
