"""IndexNow, in two real store schemas.

A store's switch, its queue and its key are its own: a product created in a
store that has IndexNow off queues nothing, the notification that goes out
names that store's primary domain and key, and the key the storefront serves
is the switched-on store's alone. The main suite has no tenant schemas.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import requests
from django_tenants.utils import tenant_context

pytestmark = pytest.mark.django_db


def _switch(tenant, *, on: bool) -> None:
    from extra_settings.models import Setting

    with tenant_context(tenant):
        Setting.objects.update_or_create(
            name="INDEXNOW_ENABLED",
            defaults={"value_type": Setting.TYPE_BOOL, "value_bool": on},
        )


def _product(tenant):
    from product.factories.product import ProductFactory

    with tenant_context(tenant):
        return ProductFactory.create(active=True, num_images=0, num_reviews=0)


def _queued(tenant) -> list[str]:
    from indexnow.models import PendingUrl

    with tenant_context(tenant):
        return list(PendingUrl.objects.values_list("path", flat=True))


@pytest.fixture
def two_stores(mt_tenant, mt_tenant_b):
    yield mt_tenant, mt_tenant_b
    from indexnow.models import PendingUrl
    from product.models.product import Product

    for tenant in (mt_tenant, mt_tenant_b):
        _switch(tenant, on=False)
        with tenant_context(tenant):
            PendingUrl.objects.all().delete()
            Product.objects.all().delete()


def test_only_the_store_that_switched_it_on_queues_its_pages(two_stores):
    on_store, off_store = two_stores
    _switch(on_store, on=True)
    _switch(off_store, on=False)

    queued_product = _product(on_store)
    _product(off_store)

    assert _queued(on_store) == [queued_product.storefront_path]
    assert _queued(off_store) == []


def test_the_notification_names_the_stores_own_host_and_key(two_stores):
    from indexnow.service import send_pending

    on_store, _off_store = two_stores
    _switch(on_store, on=True)
    product = _product(on_store)
    response = requests.Response()
    response.status_code = 200

    with tenant_context(on_store):
        with patch(
            "indexnow.service.requests.post", return_value=response
        ) as post:
            send_pending()

    body = post.call_args.kwargs["json"]
    assert body["host"] == on_store.get_primary_domain().domain
    assert body["key"] == on_store.indexnow_key
    assert all(
        url.startswith(f"https://{body['host']}/") for url in body["urlList"]
    )
    assert (
        f"https://{body['host']}{product.storefront_path}" in (body["urlList"])
    )
    assert _queued(on_store) == []


def test_only_the_switched_on_store_serves_its_key(two_stores):
    on_store, off_store = two_stores
    _switch(on_store, on=True)
    _switch(off_store, on=False)

    assert on_store.published_indexnow_key == on_store.indexnow_key
    assert off_store.published_indexnow_key == ""
    assert on_store.indexnow_key != off_store.indexnow_key
