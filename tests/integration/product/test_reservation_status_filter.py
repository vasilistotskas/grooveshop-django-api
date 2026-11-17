"""The changelist's reservation-status filter reads the active holds.

Its three choices (some active hold, none, a high share of the stock) used
to filter on ``stockreservation__...``, which is not the reverse name of
``StockReservation.product`` (its ``related_name`` is
``stock_reservations``), so choosing any of them raised ``FieldError``
and the list answered ``?e=1``. They now read
``StockReservationQuerySet.active`` through one annotation, the rule the
checkout and the product form already use.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib import admin as django_admin
from django.test import RequestFactory
from django.utils import timezone

from order.models.stock_reservation import StockReservation
from product.admin.filters import StockReservationStatusFilter
from product.admin.product import ProductAdmin
from product.factories.product import ProductFactory
from product.models.product import Product

pytestmark = pytest.mark.django_db


def _hold(product, quantity, *, consumed=False, expired=False):
    now = timezone.now()
    return StockReservation.objects.create(
        product=product,
        quantity=quantity,
        session_id=f"session-{product.pk}-{quantity}-{consumed}-{expired}",
        consumed=consumed,
        expires_at=now - timedelta(minutes=1)
        if expired
        else now + timedelta(minutes=15),
    )


def _found(choice):
    request = RequestFactory().get(
        "/admin/product/product/", {"reservation_status": choice}
    )
    flt = StockReservationStatusFilter(
        request,
        {"reservation_status": [choice]},
        Product,
        ProductAdmin(Product, django_admin.site),
    )
    return set(flt.queryset(request, Product.objects.all()))


@pytest.fixture
def products():
    def make(stock):
        return ProductFactory(num_images=0, num_reviews=0, stock=stock)

    return {
        "idle": make(10),
        "held": make(10),
        "mostly_held": make(10),
        # An unexpired hold already converted to a sale and an expired
        # hold nobody consumed: neither is active, and no single hold is
        # both, so the product has none.
        "none_active": make(10),
    }


@pytest.fixture
def holds(products):
    _hold(products["held"], 2)
    _hold(products["mostly_held"], 4)
    _hold(products["mostly_held"], 2)
    _hold(products["none_active"], 3, consumed=True)
    _hold(products["none_active"], 3, expired=True)


def test_has_reservations_lists_products_with_an_active_hold(products, holds):
    assert _found("has_reservations") == {
        products["held"],
        products["mostly_held"],
    }


def test_no_reservations_lists_products_without_an_active_hold(products, holds):
    assert _found("no_reservations") == {
        products["idle"],
        products["none_active"],
    }


def test_high_reservations_lists_products_held_above_half_the_stock(
    products, holds
):
    assert _found("high_reservations") == {products["mostly_held"]}
