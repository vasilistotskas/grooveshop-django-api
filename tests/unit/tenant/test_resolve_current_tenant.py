"""``resolve_current_tenant`` upgrades django-tenants' bare ``FakeTenant``
(what ``schema_context(name)`` binds) to the real row, so callers that
need ``plan`` or ``vertical`` get them under a shell or a command too.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django_tenants.utils import schema_context

from tenant.membership import get_current_tenant, resolve_current_tenant
from tenant.models import StoreVertical

pytestmark = pytest.mark.django_db


def test_public_schema_resolves_to_none():
    connection.set_schema_to_public()
    assert resolve_current_tenant() is None


def test_a_bound_row_is_returned_as_is(tenant_factory, bind_tenant):
    tenant = tenant_factory("resolve-real-tenant")
    bind_tenant(tenant)

    assert resolve_current_tenant() is tenant


def test_a_fake_tenant_is_upgraded_to_the_row(tenant_factory):
    tenant = tenant_factory("resolve-fake-tenant")
    tenant.vertical = StoreVertical.FASHION
    tenant.save(update_fields=["vertical"])

    with schema_context(tenant.schema_name):
        bound = get_current_tenant()
        assert bound is not None
        assert not hasattr(bound, "vertical")

        resolved = resolve_current_tenant()

    assert resolved is not None
    assert resolved.pk == tenant.pk
    assert resolved.vertical == StoreVertical.FASHION
