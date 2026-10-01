"""The product stock-history page (``ProductAdmin.stock_history_view``)."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from order.models.stock_log import StockLog
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _client(user):
    client = Client()
    client.force_login(
        user, backend="tenant.auth_backends.PlatformStaffBackend"
    )
    return client


def _url(product):
    return reverse("admin:product_product_stock_history", args=[product.pk])


def test_renders_the_movements():
    product = ProductFactory(stock=7)
    StockLog.objects.create(
        product=product,
        operation_type=StockLog.OPERATION_DECREMENT,
        quantity_delta=-3,
        stock_before=10,
        stock_after=7,
        reason="Order placed",
    )

    response = _client(UserAccountFactory(admin=True)).get(_url(product))

    assert response.status_code == 200
    html = response.content.decode()
    assert "Order placed" in html
    assert "-3" in html
    assert "var(--color-red-500)" in html
    assert "#ef4444" not in html


def test_an_unknown_window_falls_back_to_ninety_days():
    product = ProductFactory()
    response = _client(UserAccountFactory(admin=True)).get(
        _url(product), {"days": "12"}
    )
    assert response.context["window_days"] == 90


def test_needs_view_permission_on_products():
    staff = UserAccountFactory(is_staff=True)
    response = _client(staff).get(_url(ProductFactory()))
    assert response.status_code == 403
