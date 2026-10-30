"""A guest alert's confirmation token works only on the store that sent it.

The token is a signed payload naming the alert's schema and uuid, and
``SECRET_KEY`` is global: without the schema check, a token minted on one
store would verify on another's host. Asserted here, where the tenant
router and middleware are live, with two real tenant schemas.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone
from django_tenants.test.client import TenantClient
from django_tenants.utils import schema_context

pytestmark = pytest.mark.django_db


def _pending_alert(tenant):
    from product.alerts import make_confirmation_token
    from product.factories.product import ProductFactory
    from product.models.alert import ProductAlert, ProductAlertKind

    with schema_context(tenant.schema_name):
        product = ProductFactory.create(stock=0, num_images=0, num_reviews=0)
        alert = ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK,
            product=product,
            email="mt-guest@example.com",
            is_active=False,
            confirmation_sent_at=timezone.now(),
        )
        return product, alert, make_confirmation_token(alert)


def _is_active(tenant, alert):
    with schema_context(tenant.schema_name):
        alert.refresh_from_db()
        return alert.is_active


def test_another_stores_token_is_refused_here(mt_tenant, mt_tenant_b):
    product_b, alert_b, token = _pending_alert(mt_tenant_b)
    url = reverse("product-alert-confirm-by-token", args=[token])
    try:
        # Store B's real, valid, unexpired token, presented to store A.
        response = TenantClient(mt_tenant).post(url)

        assert response.status_code == 400, getattr(response, "data", None)
        assert _is_active(mt_tenant_b, alert_b) is False

        # ...while on its own store the same token confirms.
        response = TenantClient(mt_tenant_b).post(url)
        assert response.status_code == 200, getattr(response, "data", None)
        assert _is_active(mt_tenant_b, alert_b) is True
    finally:
        with schema_context(mt_tenant_b.schema_name):
            product_b.delete()
