"""A real HTTP request through ``TenantMainMiddleware`` binds
``connection.schema_name`` to the tenant resolved from the Host header
— the main suite strips this middleware entirely (``tests/conftest.py``:
"Remove TenantMainMiddleware so tests don't need a TenantDomain for
'testserver'"), so a regression in tenant resolution is invisible
there.

``TenantClient`` (``django_tenants.test.client``) sets ``HTTP_HOST`` to
the tenant's primary domain automatically.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django_tenants.test.client import TenantClient
from django_tenants.utils import schema_context


@pytest.mark.django_db
def test_tenant_client_request_binds_schema_and_sees_tenant_data(mt_tenant):
    from product.factories.product import ProductFactory

    with schema_context(mt_tenant.schema_name):
        product = ProductFactory()
        product_id = product.id

    client = TenantClient(mt_tenant)
    response = client.get(reverse("product-detail", args=[product_id]))

    assert response.status_code == 200, (
        f"expected the tenant-resolved request to find product "
        f"{product_id} in '{mt_tenant.schema_name}', got "
        f"{response.status_code}: {getattr(response, 'data', None)}"
    )
    assert response.data["id"] == product_id
