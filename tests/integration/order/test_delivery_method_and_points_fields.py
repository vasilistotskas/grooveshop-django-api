"""``delivery_method`` on the orders list, ``loyalty_points_to_earn`` on
the order detail."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from order.factories.order import OrderFactory
from shipping.enum import ShippingKind
from shipping.factories import ShippingProviderFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_list_rows_carry_the_delivery_method():
    user = UserAccountFactory(num_addresses=0)
    provider = ShippingProviderFactory(code="acs", name="ACS Courier")
    carried = OrderFactory(
        user=user,
        num_order_items=0,
        shipping_provider=provider,
        shipping_kind=ShippingKind.PICKUP_POINT,
    )
    legacy = OrderFactory(user=user, num_order_items=0, shipping_provider=None)

    response = _client(user).get(reverse("order-list"))

    assert response.status_code == status.HTTP_200_OK
    rows = {
        row["id"]: row["delivery_method"] for row in response.data["results"]
    }
    assert rows[carried.id] == {
        "provider_code": "acs",
        "provider_name": "ACS Courier",
        "kind": ShippingKind.PICKUP_POINT,
    }
    assert rows[legacy.id] == {
        "provider_code": None,
        "provider_name": None,
        "kind": ShippingKind.HOME_DELIVERY,
    }


def test_list_query_count_is_flat_in_order_count():
    user = UserAccountFactory(num_addresses=0)
    client = _client(user)
    url = reverse("order-list")

    def make(count):
        for _ in range(count):
            OrderFactory(
                user=user,
                num_order_items=0,
                shipping_provider=ShippingProviderFactory(),
            )

    def queries():
        client.get(url)  # prime caches the factories clear
        with CaptureQueriesContext(connection) as ctx:
            client.get(url)
        return len(ctx)

    make(2)
    before = queries()
    make(3)

    assert queries() == before


def test_detail_reports_the_points_the_order_earns():
    from unittest.mock import patch

    user = UserAccountFactory(num_addresses=0)
    order = OrderFactory(user=user, num_order_items=1)

    with patch(
        "loyalty.services.LoyaltyService.get_order_points", return_value=42
    ) as points:
        response = _client(user).get(reverse("order-detail", args=[order.pk]))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["loyalty_points_to_earn"] == 42
    points.assert_called_once()
