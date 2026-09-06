"""A suspended store must stop serving, not just stop resolving.

`suspend_tenant` sets `is_active=False`, and five consumers honour it:
`tenant/views.py` resolve 404s, the internal domains feed skips it, the
WebSocket middleware closes 4004, the Celery fanout skips it, and the
Viva webhook refuses it. The one path never covered was the one that
serves the store — the tenant HTTP request.

`django_tenants.middleware.main.TenantMainMiddleware.get_tenant`
resolves the host with a bare `domain=hostname` lookup, and this project
uses that class directly rather than a subclass. Measured before the
fix:

    active tenant    -> product-list: 200
    tenant is_active now: False
    SUSPENDED tenant -> product-list: 200

So a merchant suspended for abuse or non-payment went dark on the
storefront — whose SSR calls `resolve` first — while their API kept
serving in full: catalogue reads, cart mutations, order creation and
payment-intent creation against their own live provider keys.

This lane is the only place the rule is real: the main suite strips
`TenantMainMiddleware` entirely.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django_tenants.test.client import TenantClient

from tenant.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def suspended(mt_tenant):
    Tenant.objects.filter(pk=mt_tenant.pk).update(is_active=False)
    mt_tenant.refresh_from_db()
    yield mt_tenant
    Tenant.objects.filter(pk=mt_tenant.pk).update(is_active=True)
    mt_tenant.refresh_from_db()


def test_an_active_tenant_is_served(mt_tenant):
    """The control: the guard must not break a live store."""
    response = TenantClient(mt_tenant).get(reverse("product-list"))

    assert response.status_code == 200


@pytest.mark.parametrize(
    "route",
    ["product-list", "blog-post-list", "cart-list"],
)
def test_a_suspended_tenant_is_refused(suspended, route):
    response = TenantClient(suspended).get(reverse(route))

    assert response.status_code == 503, response.status_code


def test_the_refusal_explains_itself(suspended):
    """A shopper hitting a suspended store should not see a bare error."""
    response = TenantClient(suspended).get(reverse("product-list"))

    assert "detail" in response.json()
    assert response.json()["detail"]


def test_reactivating_restores_service(mt_tenant):
    Tenant.objects.filter(pk=mt_tenant.pk).update(is_active=False)
    assert (
        TenantClient(mt_tenant).get(reverse("product-list")).status_code == 503
    )

    Tenant.objects.filter(pk=mt_tenant.pk).update(is_active=True)
    mt_tenant.refresh_from_db()

    assert (
        TenantClient(mt_tenant).get(reverse("product-list")).status_code == 200
    )
