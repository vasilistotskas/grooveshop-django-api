"""A product that sells out or comes back is an edit of what the storefront
shows; an ordinary sale is not.

Every order saves ``stock`` alone (``order/stock.py``) and the product
surfaces ignore that save, so the cached listing is not emptied on each
sale. The one such save that changes what a page shows is the one that moves
a product between available and sold out: the listing puts sold-out products
last and the page builder's lists read the same product data.
"""

from __future__ import annotations

from decimal import Decimal
from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.cache import invalidation
from product import availability
from product.models.product import Product
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db

SALE = ["stock", "updated_at"]
#: The surfaces that ignore a sale and so need the flip to purge them.
PRODUCT_SURFACES = {"products", "sitemap_seo"}


@pytest.fixture(autouse=True)
def _connected():
    invalidation._pending_codes().clear()
    invalidation.connect_surface_invalidation(force=True)
    availability.connect(force=True)
    yield
    availability.disconnect()
    invalidation.disconnect_surface_invalidation()
    invalidation._pending_codes().clear()


@pytest.fixture
def stale():
    """The surfaces each save marked stale."""
    with mock.patch.object(invalidation, "_mark_stale") as mark:
        yield mark


def product(stock: int, sku: str = "P-1") -> Product:
    return Product.objects.create(
        sku=sku,
        price=Decimal("10.00"),
        active=True,
        stock=stock,
        vat=VatFactory(value=Decimal("20.0")),
    )


def marked(stale) -> set[str]:
    return {call.args[0] for call in stale.call_args_list}


class TestTheProductSurfaces:
    def test_a_sale_that_sells_the_product_out_purges_them(self, stale):
        item = product(stock=1)
        stale.reset_mock()

        item.stock = 0
        item.save(update_fields=SALE)

        assert marked(stale) >= PRODUCT_SURFACES

    def test_a_restock_purges_them(self, stale):
        item = product(stock=0)
        stale.reset_mock()

        item.stock = 7
        item.save(update_fields=["stock"])

        assert marked(stale) >= PRODUCT_SURFACES

    def test_a_sale_that_leaves_the_product_in_stock_purges_nothing(
        self, stale
    ):
        item = product(stock=5)
        stale.reset_mock()

        item.stock = 4
        item.save(update_fields=SALE)

        assert marked(stale) & PRODUCT_SURFACES == set()

    def test_a_save_of_a_sold_out_product_that_stays_so_purges_nothing(
        self, stale
    ):
        item = product(stock=0)
        stale.reset_mock()

        item.save(update_fields=SALE)

        assert marked(stale) & PRODUCT_SURFACES == set()

    def test_a_sale_that_sells_out_costs_the_queries_of_any_other_sale(
        self, stale
    ):
        """The stored stock rides the query that reads the stored category."""
        selling = product(stock=3)
        sold_out = product(stock=1, sku="P-2")
        selling.stock = 2
        selling.save(update_fields=SALE)  # the connection's first save
        selling.stock, sold_out.stock = 1, 0

        with CaptureQueriesContext(connection) as ordinary:
            selling.save(update_fields=SALE)
        with CaptureQueriesContext(connection) as flipping:
            sold_out.save(update_fields=SALE)

        assert len(flipping) == len(ordinary)

    def test_a_full_save_that_sells_out_marks_once(self, stale):
        item = product(stock=1)
        stale.reset_mock()

        item.stock = 0
        item.save()

        assert [c.args[0] for c in stale.call_args_list].count("products") == 1
