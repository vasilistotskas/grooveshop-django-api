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


class TestStoreWithoutAPrimaryDomain:
    """A store whose host cannot be found must never purge platform-wide."""

    @pytest.fixture
    def domainless(self, bound_tenant):
        bound_tenant.domains.all().delete()
        return bound_tenant

    def test_the_host_lookup_refuses_instead_of_answering_none(
        self, domainless
    ):
        with pytest.raises(nuxt_client.StoreHostUnresolvedError) as raised:
            nuxt_client._current_tenant_host()
        assert raised.value.schema_name == "purge_scope_tenant"

    def test_no_request_is_sent_and_an_error_is_logged(
        self, _configured, domainless, caplog
    ):
        with (
            caplog.at_level("ERROR", logger="core.cache.nuxt"),
            mock.patch("core.cache.nuxt.requests.post") as post,
        ):
            result = nuxt_client.request_purge(["cache:nitro:routes:_:*"])
        post.assert_not_called()
        assert result.error and "purge_scope_tenant" in result.error
        assert (result.matched, result.deleted) == (0, 0)
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(errors) == 1
        assert "purge_scope_tenant" in errors[0].getMessage()

    def test_a_store_with_only_a_secondary_domain_is_refused_too(
        self, _configured, bound_tenant
    ):
        bound_tenant.domains.filter(is_primary=True).delete()
        with mock.patch("core.cache.nuxt.requests.post") as post:
            result = nuxt_client.request_purge(["cache:nitro:routes:_:*"])
        post.assert_not_called()
        assert result.error

    def test_the_public_schema_purge_stays_platform_wide(
        self, _configured, monkeypatch
    ):
        from types import SimpleNamespace

        from django.db import connection

        monkeypatch.setattr(
            connection,
            "tenant",
            SimpleNamespace(schema_name="public"),
            raising=False,
        )
        with mock.patch("core.cache.nuxt.requests.post") as post:
            post.return_value.json.return_value = {}
            nuxt_client.request_purge(["cache:nitro:routes:_:*"])
        assert "host" not in post.call_args.kwargs["json"]


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


class TestRequestPurgeTenantConfig:
    def _post(self, **kwargs):
        posted = {}

        def _fake_post(endpoint, json=None, headers=None, timeout=None):
            posted["json"] = json
            resp = mock.Mock()
            resp.raise_for_status = mock.Mock()
            resp.json = mock.Mock(
                return_value={"matched": 0, "deleted": 0, "blocked": 0}
            )
            return resp

        with mock.patch(
            "core.cache.nuxt.requests.post", side_effect=_fake_post
        ):
            nuxt_client.request_purge(["cache:nitro:handlers:Blog*"], **kwargs)
        return posted["json"]

    def test_sends_the_flag_when_the_purge_follows_a_config_change(
        self, _configured
    ):
        assert self._post(tenant_config=True)["tenantConfig"] is True

    def test_sends_nothing_otherwise(self, _configured):
        assert "tenantConfig" not in self._post()
