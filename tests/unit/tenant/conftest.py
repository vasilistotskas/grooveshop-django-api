"""Shared fixtures for tenant-scoped unit tests.

Central place to build ``Tenant`` rows without tripping django-tenants'
``auto_create_schema`` hook (which would issue ``CREATE SCHEMA`` DDL on
every save). The DB router is already disabled in ``tests/conftest.py``
so nothing ever queries the tenant schema anyway — we just need a row
the helpers can look up.
"""

from __future__ import annotations

import pytest
from django.db import connection

from tenant.models import Tenant


def make_test_tenant(slug: str, schema_name: str) -> Tenant:
    """Create a Tenant with the schema-creation hook disabled."""
    t = Tenant(
        schema_name=schema_name,
        name=slug.replace("-", " ").title(),
        slug=slug,
        owner_email=f"owner-{slug}@example.com",
    )
    t.auto_create_schema = False
    t.save()
    return t


@pytest.fixture
def tenant_factory(db):
    """Factory so tests can build multiple tenants with unique keys."""

    def _factory(slug: str | None = None) -> Tenant:
        slug = slug or "unit-fixture-tenant"
        return make_test_tenant(slug=slug, schema_name=slug.replace("-", "_"))

    return _factory


@pytest.fixture
def bind_tenant(monkeypatch):
    """Attach a tenant to ``django.db.connection`` for a single test.

    ``django-tenants`` sets ``connection.tenant`` from the middleware
    during a real request cycle; unit tests have the middleware stripped
    (see ``tests/conftest.py``), so the helpers have nothing to read.
    This fixture swaps it in, monkeypatch unwinds it after the test so
    parallel workers don't see leaked state.
    """

    def _bind(t):
        monkeypatch.setattr(connection, "tenant", t, raising=False)

    yield _bind
