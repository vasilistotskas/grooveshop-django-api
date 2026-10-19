"""An ``Idempotency-Key`` is the store's: the same cart and key on another
store is another request.

The main suite strips ``TenantMainMiddleware`` and swaps the cache, so
whether a replay can cross schemas is invisible there. Here the request
runs the real middleware order and the production ``KEY_FUNCTION``
(``tenant.cache.make_tenant_key``), which is what keeps one store's
scopes out of another's (``core/middleware/idempotency.py``).
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from django.urls import reverse
from django_tenants.test.client import TenantClient


@pytest.mark.django_db
def test_a_key_replays_on_its_own_store_only(mt_tenant, mt_tenant_b):
    headers = {
        "HTTP_X_CART_ID": str(uuid4()),
        "HTTP_IDEMPOTENCY_KEY": str(uuid4()),
    }
    url = reverse("cart-item-list")
    # Refused, and a refusal is cached like any 4xx: no data to set up.
    body = json.dumps({})

    def post(tenant):
        return TenantClient(tenant).post(
            url, body, content_type="application/json", **headers
        )

    first = post(mt_tenant)
    assert 400 <= first.status_code < 500, first.content

    assert post(mt_tenant)["Idempotent-Replay"] == "true", (
        "the store's own retry was not answered from its first outcome"
    )
    assert post(mt_tenant_b).get("Idempotent-Replay") is None, (
        "another store answered with this store's cached response"
    )
