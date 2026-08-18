"""Tests for the public tenant API: /tenant/resolve and
/tenant/memberships/mine.

These are the two endpoints the Nuxt frontend hits at request time to
identify the current tenant and decide which admin UI to render for
the authenticated user. Breaking either takes the storefront down.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.client import Client
from rest_framework.test import APIClient

from tenant.models import (
    TenantDomain,
    TenantMembershipRole,
    UserTenantMembership,
)

User = get_user_model()


@pytest.fixture
def resolve_client():
    # The resolve endpoint is AllowAny — no auth header needed.
    return Client()


@pytest.fixture
def auth_client():
    return APIClient()


class TestTenantResolve:
    @pytest.mark.django_db
    def test_returns_tenant_config_for_primary_domain(
        self, resolve_client, tenant_factory
    ):
        tenant = tenant_factory("resolve-primary")
        TenantDomain.objects.create(
            tenant=tenant,
            domain="resolve-primary.example",
            is_primary=True,
        )
        response = resolve_client.get(
            "/api/v1/tenant/resolve?domain=resolve-primary.example"
        )
        assert response.status_code == 200
        assert response.json()["schemaName"] == "resolve_primary"

    @pytest.mark.django_db
    def test_returns_tenant_config_for_secondary_domain(
        self, resolve_client, tenant_factory
    ):
        tenant = tenant_factory("resolve-secondary")
        TenantDomain.objects.create(
            tenant=tenant,
            domain="resolve-secondary.example",
            is_primary=True,
        )
        TenantDomain.objects.create(
            tenant=tenant,
            domain="www.resolve-secondary.example",
            is_primary=False,
        )
        response = resolve_client.get(
            "/api/v1/tenant/resolve?domain=www.resolve-secondary.example"
        )
        assert response.status_code == 200
        assert response.json()["schemaName"] == "resolve_secondary"
        # primaryDomain must be the is_primary=True row, not the
        # requested www-prefixed alias — we want canonical URLs in
        # sitemap / RSS / absolute links.
        assert response.json()["primaryDomain"] == "resolve-secondary.example"

    @pytest.mark.django_db
    def test_returns_404_for_unknown_domain(self, resolve_client):
        response = resolve_client.get(
            "/api/v1/tenant/resolve?domain=nowhere.example"
        )
        assert response.status_code == 404

    @pytest.mark.django_db
    def test_returns_400_when_domain_missing(self, resolve_client):
        response = resolve_client.get("/api/v1/tenant/resolve")
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_inactive_tenant_returns_404(self, resolve_client, tenant_factory):
        tenant = tenant_factory("resolve-inactive")
        tenant.is_active = False
        tenant.save(update_fields=["is_active"])
        TenantDomain.objects.create(
            tenant=tenant,
            domain="inactive.example",
            is_primary=True,
        )
        response = resolve_client.get(
            "/api/v1/tenant/resolve?domain=inactive.example"
        )
        assert response.status_code == 404


class TestMyMemberships:
    @pytest.mark.django_db
    def test_anonymous_request_rejected(self, auth_client):
        response = auth_client.get("/api/v1/tenant/memberships/mine")
        assert response.status_code in (401, 403)

    @pytest.mark.django_db
    def test_lists_only_caller_memberships(self, auth_client, tenant_factory):
        user = User.objects.create_user(
            username="memberships-alice",
            email="memberships-alice@example.com",
            password="p",  # noqa: S106
        )
        other = User.objects.create_user(
            username="memberships-bob",
            email="memberships-bob@example.com",
            password="p",  # noqa: S106
        )
        tenant_a = tenant_factory("memb-a")
        tenant_b = tenant_factory("memb-b")
        TenantDomain.objects.create(
            tenant=tenant_a, domain="memb-a.example", is_primary=True
        )
        TenantDomain.objects.create(
            tenant=tenant_b, domain="memb-b.example", is_primary=True
        )
        UserTenantMembership.objects.create(
            user=user, tenant=tenant_a, role=TenantMembershipRole.OWNER
        )
        UserTenantMembership.objects.create(
            user=user, tenant=tenant_b, role=TenantMembershipRole.MEMBER
        )
        # Other user has their own membership in tenant_a; must not
        # appear in alice's response.
        UserTenantMembership.objects.create(
            user=other, tenant=tenant_a, role=TenantMembershipRole.ADMIN
        )

        auth_client.force_authenticate(user=user)
        response = auth_client.get("/api/v1/tenant/memberships/mine")

        assert response.status_code == 200
        payload = response.json()
        schemas = {m["schemaName"] for m in payload}
        assert schemas == {"memb_a", "memb_b"}
        role_by_schema = {m["schemaName"]: m["role"] for m in payload}
        assert role_by_schema["memb_a"] == "owner"
        assert role_by_schema["memb_b"] == "member"

    @pytest.mark.django_db
    def test_omits_inactive_memberships(self, auth_client, tenant_factory):
        user = User.objects.create_user(
            username="memb-inactive",
            email="memb-inactive@example.com",
            password="p",  # noqa: S106
        )
        tenant = tenant_factory("memb-inactive-t")
        TenantDomain.objects.create(
            tenant=tenant, domain="memb-inactive.example", is_primary=True
        )
        UserTenantMembership.objects.create(
            user=user,
            tenant=tenant,
            role=TenantMembershipRole.MEMBER,
            is_active=False,
        )
        auth_client.force_authenticate(user=user)

        response = auth_client.get("/api/v1/tenant/memberships/mine")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.django_db
    def test_omits_inactive_tenants(self, auth_client, tenant_factory):
        user = User.objects.create_user(
            username="memb-tenant-off",
            email="memb-tenant-off@example.com",
            password="p",  # noqa: S106
        )
        tenant = tenant_factory("memb-tenant-off-t")
        tenant.is_active = False
        tenant.save(update_fields=["is_active"])
        TenantDomain.objects.create(
            tenant=tenant, domain="memb-tenant-off.example", is_primary=True
        )
        UserTenantMembership.objects.create(
            user=user,
            tenant=tenant,
            role=TenantMembershipRole.MEMBER,
            is_active=True,
        )
        auth_client.force_authenticate(user=user)

        response = auth_client.get("/api/v1/tenant/memberships/mine")

        assert response.status_code == 200
        assert response.json() == []


class TestTenantResolveOnPublicSchema:
    @pytest.mark.django_db
    def test_always_queries_public_schema(
        self, resolve_client, tenant_factory, monkeypatch
    ):
        # Even if a request arrives with a tenant bound to connection
        # (e.g. because middleware ran before the URL dispatch in some
        # edge case), resolve must hit public — it IS the lookup that
        # drives tenant resolution, so it cannot itself depend on a
        # tenant already being selected.
        tenant = tenant_factory("resolve-public-check")
        TenantDomain.objects.create(
            tenant=tenant, domain="public-check.example", is_primary=True
        )

        # Swap connection.tenant to a sentinel: if the view ever reads
        # tenant-schema data we want the test to blow up visibly.
        sentinel_tenant = object()
        monkeypatch.setattr(
            connection, "tenant", sentinel_tenant, raising=False
        )

        response = resolve_client.get(
            "/api/v1/tenant/resolve?domain=public-check.example"
        )
        assert response.status_code == 200


class TestTenantResolveChatApiKey:
    """The chat credential rides ONLY on gateway-authenticated responses.

    Ported from main's settings-backed test_resolve.py onto the
    DB-backed implementation: the key now lives on the Tenant row and
    the cache must hold exactly the public payload.
    """

    def _tenant_with_key(self, tenant_factory, slug, domain):
        tenant = tenant_factory(slug)
        tenant.chat_api_key = "tenant-chat-key"
        tenant.acp_bearer_token = "tenant-acp-token"
        tenant.save(update_fields=["chat_api_key", "acp_bearer_token"])
        TenantDomain.objects.create(
            tenant=tenant, domain=domain, is_primary=True
        )
        return tenant

    @pytest.mark.django_db
    def test_chat_api_key_requires_the_internal_secret(
        self, resolve_client, tenant_factory, settings
    ):
        settings.AGENT_GATEWAY_INTERNAL_SECRET = "gw-secret"
        self._tenant_with_key(
            tenant_factory, "chat-key-tenant", "chat-key.example"
        )
        url = "/api/v1/tenant/resolve?domain=chat-key.example"

        public = resolve_client.get(url)
        assert "chatApiKey" not in public.json()

        forged = resolve_client.get(url, HTTP_X_INTERNAL_TOKEN="wrong")
        assert "chatApiKey" not in forged.json()

        gateway = resolve_client.get(url, HTTP_X_INTERNAL_TOKEN="gw-secret")
        assert gateway.json()["chatApiKey"] == "tenant-chat-key"
        assert gateway.json()["acpBearerToken"] == "tenant-acp-token"

        # The gateway request must not have poisoned the public cache.
        from django.core.cache import cache

        after = resolve_client.get(url)
        assert "chatApiKey" not in after.json()
        assert "acpBearerToken" not in after.json()
        cached = cache.get("tenant_resolve:chat-key.example")
        assert cached is not None
        assert "chat_api_key" not in cached
        assert "acp_bearer_token" not in cached

    @pytest.mark.django_db
    def test_chat_api_key_withheld_when_no_internal_secret_configured(
        self, resolve_client, tenant_factory, settings
    ):
        # An empty AGENT_GATEWAY_INTERNAL_SECRET must never match an
        # empty header — the secret stays withheld entirely.
        settings.AGENT_GATEWAY_INTERNAL_SECRET = ""
        self._tenant_with_key(
            tenant_factory, "chat-key-nosecret", "chat-nosecret.example"
        )
        response = resolve_client.get(
            "/api/v1/tenant/resolve?domain=chat-nosecret.example",
            HTTP_X_INTERNAL_TOKEN="",
        )
        assert "chatApiKey" not in response.json()

    @pytest.mark.django_db
    def test_keyless_tenant_serves_empty_key_to_the_gateway(
        self, resolve_client, tenant_factory, settings
    ):
        settings.AGENT_GATEWAY_INTERNAL_SECRET = "gw-secret"
        tenant = tenant_factory("chat-keyless")
        TenantDomain.objects.create(
            tenant=tenant, domain="chat-keyless.example", is_primary=True
        )
        response = resolve_client.get(
            "/api/v1/tenant/resolve?domain=chat-keyless.example",
            HTTP_X_INTERNAL_TOKEN="gw-secret",
        )
        assert response.json()["chatApiKey"] == ""


class TestInternalDomains:
    """Internal-token-gated domain feed for the media-stream service."""

    @pytest.mark.django_db
    def test_anonymous_gets_404(self, resolve_client, settings):
        settings.AGENT_GATEWAY_INTERNAL_SECRET = "gw-secret"
        response = resolve_client.get("/api/v1/tenant/internal/domains")
        assert response.status_code == 404

    @pytest.mark.django_db
    def test_internal_token_returns_domains_with_service_subdomains(
        self, resolve_client, tenant_factory, settings
    ):
        settings.AGENT_GATEWAY_INTERNAL_SECRET = "gw-secret"
        tenant = tenant_factory("domains-feed")
        TenantDomain.objects.create(
            tenant=tenant, domain="feed.example", is_primary=True
        )
        TenantDomain.objects.create(
            tenant=tenant, domain="alias.feed.example", is_primary=False
        )

        response = resolve_client.get(
            "/api/v1/tenant/internal/domains",
            HTTP_X_INTERNAL_TOKEN="gw-secret",
        )
        assert response.status_code == 200
        domains = set(response.json()["domains"])
        # Primary domain + derived service subdomains; aliases verbatim.
        assert {
            "feed.example",
            "api.feed.example",
            "assets.feed.example",
            "static.feed.example",
            "alias.feed.example",
        } <= domains

    @pytest.mark.django_db
    def test_suspended_tenants_excluded(
        self, resolve_client, tenant_factory, settings
    ):
        from django.utils import timezone

        settings.AGENT_GATEWAY_INTERNAL_SECRET = "gw-secret"
        tenant = tenant_factory("domains-suspended")
        tenant.suspended_at = timezone.now()
        tenant.save(update_fields=["suspended_at"])
        TenantDomain.objects.create(
            tenant=tenant, domain="suspended.example", is_primary=True
        )

        response = resolve_client.get(
            "/api/v1/tenant/internal/domains",
            HTTP_X_INTERNAL_TOKEN="gw-secret",
        )
        assert "suspended.example" not in response.json()["domains"]
