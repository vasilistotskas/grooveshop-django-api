"""CORS admits the platform origins and the current tenant's own domains,
and nothing else. Credentialed allow-all is never an option."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient

from tenant.middleware import origin_belongs_to_tenant
from tenant.models import TenantDomain
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)

pytestmark = pytest.mark.django_db

TENANT_ORIGIN = "https://shop.cors-tenant.example"
FOREIGN_ORIGIN = "https://evil.example"


@pytest.fixture
def tenant():
    t = store_tenant("cors_tenant")
    TenantDomain.objects.create(
        tenant=t, domain="shop.cors-tenant.example", is_primary=True
    )
    TenantDomain.objects.create(
        tenant=t, domain="api.shop.cors-tenant.example", is_primary=False
    )
    previous = bind_store_tenant(t)
    yield t
    unbind_store_tenant(previous)


@pytest.fixture
def client():
    return APIClient()


def _url():
    return reverse("product-list")


def test_settings_never_allow_all_origins():
    assert getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False) is False
    assert settings.CORS_ALLOW_CREDENTIALS is True


def test_tenant_origin_is_echoed_with_credentials(tenant, client):
    response = client.get(_url(), HTTP_ORIGIN=TENANT_ORIGIN)
    assert response["Access-Control-Allow-Origin"] == TENANT_ORIGIN
    assert response["Access-Control-Allow-Credentials"] == "true"
    assert "origin" in response["Vary"].lower()


def test_secondary_tenant_domain_is_an_origin_too(tenant, client):
    origin = "https://api.shop.cors-tenant.example"
    response = client.get(_url(), HTTP_ORIGIN=origin)
    assert response["Access-Control-Allow-Origin"] == origin


def test_foreign_origin_gets_no_cors_headers(tenant, client):
    response = client.get(_url(), HTTP_ORIGIN=FOREIGN_ORIGIN)
    assert not response.has_header("Access-Control-Allow-Origin")
    assert not response.has_header("Access-Control-Allow-Credentials")


@pytest.fixture
def other_tenant():
    """Created BEFORE ``tenant`` binds the connection: a Tenant row can
    only be inserted from the public schema."""
    other = store_tenant("cors_other")
    TenantDomain.objects.create(
        tenant=other, domain="shop.other.example", is_primary=True
    )
    return other


def test_another_tenants_domain_is_foreign_here(other_tenant, tenant, client):
    response = client.get(_url(), HTTP_ORIGIN="https://shop.other.example")
    assert not response.has_header("Access-Control-Allow-Origin")


def test_preflight_from_tenant_origin(tenant, client):
    response = client.options(
        _url(),
        HTTP_ORIGIN=TENANT_ORIGIN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="x-session-token",
    )
    assert response.status_code == 200
    assert response["Access-Control-Allow-Origin"] == TENANT_ORIGIN
    allowed = response["Access-Control-Allow-Headers"].lower()
    for header in ("x-session-token", "x-cart-id", "idempotency-key"):
        assert header in allowed
    assert "PATCH" in response["Access-Control-Allow-Methods"]
    assert response["Access-Control-Max-Age"] == "600"


def test_preflight_from_foreign_origin_is_not_allowed(tenant, client):
    response = client.options(
        _url(),
        HTTP_ORIGIN=FOREIGN_ORIGIN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
    )
    assert not response.has_header("Access-Control-Allow-Origin")


def test_platform_origin_needs_no_tenant(client):
    response = client.get(_url(), HTTP_ORIGIN=settings.NUXT_BASE_URL)
    assert response["Access-Control-Allow-Origin"] == settings.NUXT_BASE_URL


def test_domain_cache_is_populated_and_invalidated(tenant, client):
    key = f"global:tenant_domains:{tenant.schema_name}"
    cache.delete(key)
    client.get(_url(), HTTP_ORIGIN=TENANT_ORIGIN)
    assert cache.get(key) == {
        "shop.cors-tenant.example",
        "api.shop.cors-tenant.example",
    }
    TenantDomain.objects.create(
        tenant=tenant, domain="www.cors-tenant.example", is_primary=False
    )
    assert cache.get(key) is None
    response = client.get(_url(), HTTP_ORIGIN="https://www.cors-tenant.example")
    assert (
        response["Access-Control-Allow-Origin"]
        == "https://www.cors-tenant.example"
    )


class TestOriginRule:
    def test_empty_origin_or_no_tenant(self, tenant):
        assert origin_belongs_to_tenant(tenant, "") is False
        assert origin_belongs_to_tenant(None, TENANT_ORIGIN) is False

    def test_exact_scheme_and_host(self, tenant):
        assert origin_belongs_to_tenant(tenant, TENANT_ORIGIN) is True
        assert (
            origin_belongs_to_tenant(tenant, "http://shop.cors-tenant.example")
            is False
        )
        assert (
            origin_belongs_to_tenant(
                tenant, "https://shop.cors-tenant.example:8443"
            )
            is False
        )
        assert (
            origin_belongs_to_tenant(
                tenant, "https://shop.cors-tenant.example.evil"
            )
            is False
        )

    def test_plain_http_is_a_debug_only_door(self, tenant, settings):
        """Production storefronts are HTTPS-only; a plain-http origin is
        admitted for local development alone."""
        origin = "http://shop.cors-tenant.example"
        assert origin_belongs_to_tenant(tenant, origin) is False
        settings.DEBUG = True
        assert origin_belongs_to_tenant(tenant, origin) is True

    def test_fake_tenant_owns_no_origins(self):
        from django_tenants.utils import get_public_schema_name
        from django_tenants.postgresql_backend.base import FakeTenant

        fake = FakeTenant(schema_name=get_public_schema_name())
        assert origin_belongs_to_tenant(fake, TENANT_ORIGIN) is False
