"""Through the real ``TenantMainMiddleware`` — which binds the tenant
BEFORE ``CorsMiddleware`` runs — a tenant storefront origin gets
credentialed CORS on its own API host and a foreign origin gets nothing.
The main lane strips that middleware, so the ordering is only proven here.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django_tenants.test.client import TenantClient

from tests_mt.conftest import MT_TENANT_DOMAIN

TENANT_ORIGIN = f"https://{MT_TENANT_DOMAIN}"


@pytest.mark.django_db
def test_tenant_origin_is_allowed_on_its_own_host(mt_tenant):
    client = TenantClient(mt_tenant)
    response = client.get(reverse("product-list"), HTTP_ORIGIN=TENANT_ORIGIN)
    assert response.status_code == 200
    assert response["Access-Control-Allow-Origin"] == TENANT_ORIGIN
    assert response["Access-Control-Allow-Credentials"] == "true"


@pytest.mark.django_db
def test_foreign_origin_is_refused(mt_tenant):
    client = TenantClient(mt_tenant)
    response = client.get(
        reverse("product-list"), HTTP_ORIGIN="https://other.test"
    )
    assert response.status_code == 200
    assert not response.has_header("Access-Control-Allow-Origin")


@pytest.mark.django_db
def test_preflight_from_tenant_origin(mt_tenant):
    client = TenantClient(mt_tenant)
    response = client.options(
        reverse("product-list"),
        HTTP_ORIGIN=TENANT_ORIGIN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
    )
    assert response.status_code == 200
    assert response["Access-Control-Allow-Origin"] == TENANT_ORIGIN
