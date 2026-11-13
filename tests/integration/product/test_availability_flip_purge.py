"""A product that sells out purges the storefront through the real batched
machinery: the save, the commit, the queued purge and what it sends.

Only the outbound calls to the storefront, the gateway and the edge are
stubbed. Real commits (``transaction=True``): the purge is queued on commit,
which the suite's autouse fixture otherwise runs at once.
"""

from __future__ import annotations

from decimal import Decimal
from unittest import mock

import pytest
from django.db import transaction

from builder import data_purge
from builder.models import BuilderReference, ReferenceKind
from core.cache import invalidation
from product import availability
from product.models.product import Product
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db(transaction=True)

SALE = ["stock", "updated_at"]
PAGE = "page:everything"
DATA = "cache:nitro:functions:builderDataProduct*"


@pytest.fixture(autouse=True)
def _connected():
    invalidation._pending_codes().clear()
    invalidation.connect_surface_invalidation(force=True)
    availability.connect(force=True)
    data_purge.connect(force=True)
    yield
    data_purge.disconnect()
    availability.disconnect()
    invalidation.disconnect_surface_invalidation()
    invalidation._pending_codes().clear()


@pytest.fixture
def storefront(settings):
    """The calls the purge makes to the storefront, in order."""
    # ``mark_surface_stale`` (the builder documents' way in) is skipped while
    # caching is disabled, which the suite does by default.
    settings.DISABLE_CACHE = False
    with (
        mock.patch("core.tasks.invalidate_cache_surfaces_task.apply_async"),
        mock.patch("core.cache.service.nuxt_client") as nuxt,
        mock.patch("core.cache.service.gateway_client") as gateway,
        mock.patch("core.cache.service.edge_cache"),
        mock.patch("core.cache.service.cache_instance") as cache_instance,
    ):
        nuxt.request_purge.return_value = mock.Mock(
            matched=0, deleted=0, blocked=0, error=None
        )
        gateway.invalidate_feeds.return_value = mock.Mock(removed=0, error=None)
        cache_instance.keys.return_value = []
        yield nuxt.request_purge


def product(stock: int) -> Product:
    item = Product.objects.create(
        sku="P-1",
        price=Decimal("10.00"),
        active=True,
        stock=stock,
        vat=VatFactory(value=Decimal("20.0")),
    )
    BuilderReference.objects.create(
        kind=ReferenceKind.QUERY_PRODUCT,
        target="*",
        document=PAGE,
        node_path="/x",
    )
    return item


def purge(storefront):
    """Everything queued by the writes so far, purged; the requests sent."""
    storefront.reset_mock()
    invalidation.run_queued_purge()
    return storefront.call_args_list


def patterns(call) -> list[str]:
    return call.args[0]


def documents(call) -> list[str]:
    return call.kwargs.get("builder_documents") or []


def test_a_sale_that_sells_out_purges_the_data_then_the_pages(storefront):
    item = product(stock=1)
    purge(storefront)

    with transaction.atomic():
        item.stock = 0
        item.save(update_fields=SALE)
    calls = purge(storefront)

    data = [i for i, c in enumerate(calls) if DATA in patterns(c)]
    pages = [i for i, c in enumerate(calls) if PAGE in documents(c)]
    assert data, "the product data cache was not purged"
    assert pages, "the page that lists products was not purged"
    assert max(data) < min(pages)
    assert documents(calls[-1]) == [PAGE]


def test_a_restock_purges_the_same(storefront):
    item = product(stock=0)
    purge(storefront)

    with transaction.atomic():
        item.stock = 5
        item.save(update_fields=SALE)
    calls = purge(storefront)

    assert any(DATA in patterns(c) for c in calls)
    assert any(PAGE in documents(c) for c in calls)


def test_an_ordinary_sale_sends_nothing(storefront):
    item = product(stock=5)
    purge(storefront)

    with transaction.atomic():
        item.stock = 4
        item.save(update_fields=SALE)

    assert purge(storefront) == []


def test_an_ordinary_edit_purges_the_data_and_the_pages_too(storefront):
    """The keys are queued before anything can drain them on this path
    as well."""
    item = product(stock=5)
    purge(storefront)

    with transaction.atomic():
        item.price = Decimal("12.00")
        item.save()
    calls = purge(storefront)

    assert any(DATA in patterns(c) for c in calls)
    assert documents(calls[-1]) == [PAGE]
