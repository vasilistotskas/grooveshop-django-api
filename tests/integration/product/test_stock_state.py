"""One definition of a product's stock state (issue #589).

Out of stock is stock 0; low is stock above 0 and at or below the
product's own ``low_stock_threshold``; anything else is in stock. A
threshold of 0 turns the alerts off, so such a product is never low. The
admin filter, the changelist badge, the stock-history page, the
low-stock email and the dashboard counter all read that one rule.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.core import mail
from django.test import Client, RequestFactory
from django.urls import reverse

from admin.dashboard.store import queries
from product.admin import ProductAdmin, StockStatusFilter
from product.enum.stock import StockState
from product.factories.product import ProductFactory
from product.models.product import Product
from product.tasks import check_low_stock_products
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

OUT = StockState.OUT_OF_STOCK
LOW = StockState.LOW_STOCK
IN = StockState.IN_STOCK

#: (low_stock_threshold, stock) -> the state every surface must show.
MATRIX: dict[tuple[int, int], StockState] = {
    (0, 0): OUT,
    (0, 4): IN,
    (0, 15): IN,
    (0, 25): IN,
    (5, 0): OUT,
    (5, 4): LOW,
    (5, 15): IN,
    (5, 25): IN,
    (20, 0): OUT,
    (20, 4): LOW,
    (20, 15): LOW,
    (20, 25): IN,
}


@pytest.fixture
def products():
    return {
        (threshold, stock): ProductFactory(
            active=True,
            num_images=0,
            num_reviews=0,
            stock=stock,
            low_stock_threshold=threshold,
            low_stock_alert_sent=False,
        )
        for threshold, stock in MATRIX
    }


def _admin_request():
    request = RequestFactory().get("/admin/")
    request.user = Mock(is_authenticated=True, is_superuser=True)
    return request


def test_the_model_property_follows_the_threshold(products):
    for key, product in products.items():
        assert product.stock_state == MATRIX[key], key


def test_the_queryset_partitions_products_by_the_same_rule(products):
    for state in StockState:
        expected = {products[k].pk for k, s in MATRIX.items() if s == state}
        found = set(
            Product.objects.by_stock_state(state).values_list("pk", flat=True)
        )
        assert found == expected, state
    assert set(Product.objects.low_stock().values_list("pk", flat=True)) == {
        products[k].pk for k, s in MATRIX.items() if s == LOW
    }


def test_the_admin_filter_offers_the_states_and_agrees(products):
    model_admin = ProductAdmin(Product, AdminSite())
    request = _admin_request()
    lookups = StockStatusFilter(request, {}, Product, model_admin).lookups(
        request, model_admin
    )
    assert [value for value, _label in lookups] == list(StockState.values)

    for state in StockState:
        stock_filter = StockStatusFilter(
            request, {"stock_status": [state.value]}, Product, model_admin
        )
        found = set(
            stock_filter.queryset(request, Product.objects.all()).values_list(
                "pk", flat=True
            )
        )
        assert found == {
            products[k].pk for k, s in MATRIX.items() if s == state
        }, state


def test_the_dashboard_link_opens_the_filtered_changelist(products):
    client = Client()
    client.force_login(
        UserAccountFactory(admin=True),
        backend="tenant.auth_backends.PlatformStaffBackend",
    )
    response = client.get(
        reverse("admin:product_product_changelist"),
        {"active__exact": 1, "stock_status": LOW},
    )
    assert response.status_code == 200
    assert {p.pk for p in response.context["cl"].result_list} == {
        products[k].pk for k, s in MATRIX.items() if s == LOW
    }


def test_the_changelist_badge_agrees(products):
    model_admin = ProductAdmin(Product, AdminSite())
    for key, product in products.items():
        state, label = model_admin.stock_status(product)
        assert state == MATRIX[key], key
        assert str(product.stock) in str(label)


def test_the_stock_history_page_agrees(products):
    client = Client()
    client.force_login(
        UserAccountFactory(admin=True),
        backend="tenant.auth_backends.PlatformStaffBackend",
    )
    for key, product in products.items():
        response = client.get(
            reverse("admin:product_product_stock_history", args=[product.pk])
        )
        assert response.status_code == 200
        assert response.context["stock_state"] == MATRIX[key], key


def test_the_low_stock_email_alerts_low_and_sold_out_with_alerts_on(
    products,
):
    mail.outbox.clear()
    with patch(
        "emails.recipients.store_operator_addresses",
        return_value=["admin@example.com"],
    ):
        result = check_low_stock_products()

    alerted = {
        key
        for key, product in products.items()
        if Product.objects.get(pk=product.pk).low_stock_alert_sent
    }
    assert alerted == {
        (threshold, stock)
        for (threshold, stock), state in MATRIX.items()
        if threshold > 0 and state != IN
    }
    assert result["alerted"] == len(alerted)


def test_the_dashboard_counts_the_low_products_only():
    for stock in (0, 4, 15, 25):
        ProductFactory(active=True, stock=stock, low_stock_threshold=20)
    ProductFactory(active=True, stock=15, low_stock_threshold=0)
    ProductFactory(active=False, stock=4, low_stock_threshold=20)

    assert queries.attention_counts.compute()["low_stock"] == 2
