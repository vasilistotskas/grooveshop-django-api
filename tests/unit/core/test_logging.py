"""Tests for ``core.logging.TenantContextFilter``.

Defensive re-implementation of ``django_tenants.log.TenantContextFilter``
— the vendored filter dereferences ``connection.tenant.schema_name``
unconditionally, which raises ``AttributeError`` (crashing the log
call itself) whenever no tenant is bound. ``connection.tenant`` is
routinely absent outside a request handled by ``TenantMainMiddleware``
(management commands, Celery tasks, app startup) — the common case in
this codebase — so this filter must never raise there.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from django.db import connection

from core.logging import TenantContextFilter


@pytest.fixture
def bind_tenant(monkeypatch):
    def _bind(t):
        monkeypatch.setattr(connection, "tenant", t, raising=False)

    return _bind


def _record():
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )


class TestTenantContextFilter:
    def test_attaches_schema_and_domain_when_tenant_bound(self, bind_tenant):
        bind_tenant(
            SimpleNamespace(schema_name="acme", domain_url="acme.example.com")
        )
        record = _record()
        assert TenantContextFilter().filter(record) is True
        assert record.schema_name == "acme"
        assert record.domain_url == "acme.example.com"

    def test_falls_back_to_sentinel_when_no_tenant_bound(self, bind_tenant):
        bind_tenant(None)
        record = _record()
        assert TenantContextFilter().filter(record) is True
        assert record.schema_name == "-"
        assert record.domain_url == "-"

    def test_falls_back_to_sentinel_when_tenant_attribute_missing(
        self, monkeypatch
    ):
        monkeypatch.delattr(connection, "tenant", raising=False)
        record = _record()
        assert TenantContextFilter().filter(record) is True
        assert record.schema_name == "-"
        assert record.domain_url == "-"

    def test_falls_back_to_sentinel_when_domain_url_missing(self, bind_tenant):
        bind_tenant(SimpleNamespace(schema_name="acme"))
        record = _record()
        assert TenantContextFilter().filter(record) is True
        assert record.schema_name == "acme"
        assert record.domain_url == "-"

    def test_never_raises_and_always_returns_true(self, bind_tenant):
        """Never drops a record — a filter that suppressed log lines on
        error would be worse than one that just tags them generically."""
        bind_tenant(SimpleNamespace())  # no schema_name, no domain_url
        record = _record()
        assert TenantContextFilter().filter(record) is True
