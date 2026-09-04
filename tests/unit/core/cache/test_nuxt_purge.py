"""The Nuxt cache-purge client scopes evictions to the calling tenant.

The SSR cache is shared across every tenant. A merchant purge must
carry that store's host so the Nuxt endpoint evicts only its keys,
never every tenant's — and a platform (public-schema) purge must stay
global (no host).
"""

from __future__ import annotations

from unittest import mock

import pytest

from core.cache import nuxt as nuxt_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def _configured(settings):
    settings.NUXT_INTERNAL_BASE_URL = "http://frontend-nuxt-service:80"
    settings.NUXT_CACHE_PURGE_TOKEN = "secret-token"


@pytest.fixture
def bound_tenant(db, monkeypatch):
    from django.db import connection

    from tenant.models import Tenant, TenantDomain

    tenant = Tenant(
        schema_name="purge_scope_tenant",
        name="Purge Scope Tenant",
        slug="purge-scope-tenant",
        owner_email="owner-purge-scope@example.com",
    )
    tenant.auto_create_schema = False
    tenant.save()
    TenantDomain.objects.create(
        domain="purge-scope.example", tenant=tenant, is_primary=True
    )
    TenantDomain.objects.create(
        domain="api.purge-scope.example", tenant=tenant, is_primary=False
    )
    monkeypatch.setattr(connection, "tenant", tenant, raising=False)
    return tenant


class TestCurrentTenantHost:
    def test_returns_primary_domain_for_a_bound_tenant(self, bound_tenant):
        assert nuxt_client._current_tenant_host() == "purge-scope.example"

    def test_none_without_a_tenant(self, monkeypatch):
        from django.db import connection

        monkeypatch.setattr(connection, "tenant", None, raising=False)
        assert nuxt_client._current_tenant_host() is None


class TestRequestPurgeHostScoping:
    def _capture_payload(self):
        posted = {}

        def _fake_post(endpoint, json=None, headers=None, timeout=None):
            posted["json"] = json
            resp = mock.Mock()
            resp.raise_for_status = mock.Mock()
            resp.json = mock.Mock(
                return_value={"matched": 0, "deleted": 0, "blocked": 0}
            )
            return resp

        return posted, _fake_post

    def test_tenant_purge_sends_its_host(self, _configured, bound_tenant):
        posted, fake_post = self._capture_payload()
        with mock.patch("core.cache.nuxt.requests.post", side_effect=fake_post):
            nuxt_client.request_purge(["cache:nitro:handlers:Blog*"])
        assert posted["json"]["host"] == "purge-scope.example"

    def test_platform_purge_sends_no_host(self, _configured, monkeypatch):
        from django.db import connection

        monkeypatch.setattr(connection, "tenant", None, raising=False)
        posted, fake_post = self._capture_payload()
        with mock.patch("core.cache.nuxt.requests.post", side_effect=fake_post):
            nuxt_client.request_purge(["cache:nitro:handlers:Blog*"])
        assert "host" not in posted["json"]
