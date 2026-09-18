"""``?ordering=`` on the account's order list.

``my_orders`` is an extra action that lists the viewset's OWN model
through ``filter_queryset``, so it keeps the full Order contract — it is
the one extra action that sorted correctly before the per-action
contract existed, and the storefront's orders page depends on it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from user.factories import UserAccountFactory


def _ids(response) -> list[int]:
    assert response.status_code == 200, response.content
    return [row["id"] for row in response.json()["results"]]


@pytest.mark.django_db
def test_my_orders_sort_both_ways_and_by_status():
    user = UserAccountFactory()
    client = APIClient()
    client.force_authenticate(user=user)
    older = OrderFactory(user=user, status=OrderStatus.PENDING)
    newer = OrderFactory(user=user, status=OrderStatus.COMPLETED)
    Order.objects.filter(pk=older.pk).update(
        created_at=timezone.now() - timedelta(days=2)
    )
    url = reverse("order-my-orders")

    assert _ids(client.get(url, {"ordering": "createdAt"})) == [
        older.pk,
        newer.pk,
    ]
    assert _ids(client.get(url, {"ordering": "-createdAt"})) == [
        newer.pk,
        older.pk,
    ]
    # COMPLETED < PENDING alphabetically.
    assert _ids(client.get(url, {"ordering": "status"})) == [newer.pk, older.pk]
