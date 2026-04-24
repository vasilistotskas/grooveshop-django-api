"""Tests for ``core.utils.tenant_urls``.

The helpers read ``connection.tenant`` (set by
``django_tenants.TenantMainMiddleware`` in production and by
``TenantTask.__call__`` in Celery). Callers use them in place of
``settings.NUXT_BASE_URL`` so outbound emails / push notifications /
WebSocket link-backs resolve to the requesting tenant's domain.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.db import connection
from django.test import override_settings

from core.utils.tenant_urls import get_tenant_base_url, get_tenant_frontend_url


@pytest.fixture
def bind_tenant(monkeypatch):
    def _bind(t):
        monkeypatch.setattr(connection, "tenant", t, raising=False)

    yield _bind


def _fake_tenant(primary_domain: str, schema_name: str = "tenant_a"):
    """Build the minimum tenant shape the helpers touch."""
    primary_domain_obj = SimpleNamespace(domain=primary_domain)
    domains_qs = MagicMock()
    domains_qs.filter.return_value.first.return_value = primary_domain_obj
    return SimpleNamespace(schema_name=schema_name, domains=domains_qs)


class TestGetTenantBaseUrl:
    def test_uses_tenant_primary_domain(self, bind_tenant):
        bind_tenant(_fake_tenant(primary_domain="tenant-b.com"))
        assert get_tenant_base_url() == "https://tenant-b.com"

    @override_settings(NUXT_BASE_URL="https://fallback.example")
    def test_fallback_to_settings_when_no_tenant(self, bind_tenant):
        bind_tenant(None)
        assert get_tenant_base_url() == "https://fallback.example"

    @override_settings(NUXT_BASE_URL="https://fallback.example/")
    def test_fallback_strips_trailing_slash(self, bind_tenant):
        bind_tenant(None)
        # Trailing slash on NUXT_BASE_URL is idempotent — rstrip in the
        # helper prevents `//path` when the caller concatenates.
        assert get_tenant_base_url() == "https://fallback.example"

    def test_fallback_to_settings_when_no_primary_domain(self, bind_tenant):
        tenant = SimpleNamespace(schema_name="tenant_a")
        domains_qs = MagicMock()
        domains_qs.filter.return_value.first.return_value = None
        tenant.domains = domains_qs
        bind_tenant(tenant)

        with override_settings(NUXT_BASE_URL="https://fallback.example"):
            assert get_tenant_base_url() == "https://fallback.example"


class TestGetTenantFrontendUrl:
    def test_prepends_leading_slash_when_missing(self, bind_tenant):
        bind_tenant(_fake_tenant(primary_domain="webside.gr"))
        assert (
            get_tenant_frontend_url("account/orders/42")
            == "https://webside.gr/account/orders/42"
        )

    def test_respects_leading_slash_when_present(self, bind_tenant):
        bind_tenant(_fake_tenant(primary_domain="webside.gr"))
        assert (
            get_tenant_frontend_url("/account/orders/42")
            == "https://webside.gr/account/orders/42"
        )

    def test_empty_path_returns_base_url(self, bind_tenant):
        bind_tenant(_fake_tenant(primary_domain="webside.gr"))
        assert get_tenant_frontend_url("") == "https://webside.gr"

    def test_switches_tenants_per_call(self, bind_tenant):
        bind_tenant(_fake_tenant(primary_domain="tenant-a.example"))
        first = get_tenant_frontend_url("/cart")
        assert first == "https://tenant-a.example/cart"

        bind_tenant(_fake_tenant(primary_domain="tenant-b.example"))
        second = get_tenant_frontend_url("/cart")
        assert second == "https://tenant-b.example/cart"
