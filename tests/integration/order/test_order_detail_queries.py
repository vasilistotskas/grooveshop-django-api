"""The order detail response must not re-aggregate its own totals.

``Order.total_price_items`` is annotation-backed only when the annotation
is present. Without it each read costs an aggregate plus a currency
lookup — and ``aggregate()`` bypasses the prefetch cache, so eager-loading
the items does not help. The detail serializer reads it three or four
times per response, and ``for_detail()`` did not carry the annotation
even though its docstring claimed it included everything the list had.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def order_with_lines():
    user = UserAccountFactory(num_addresses=0)
    order = OrderFactory(user=user, num_order_items=0)
    for _ in range(3):
        OrderItemFactory(
            order=order, product=ProductFactory(num_images=0, num_reviews=0)
        )
    return order


def test_detail_carries_the_totals_annotation(order_with_lines):
    """The annotation is what keeps the serializer off the aggregate."""
    from order.models.order import Order

    fetched = Order.objects.for_detail().get(pk=order_with_lines.pk)

    assert "items_total" in fetched.__dict__, (
        "for_detail() lost the totals annotation, so every read of "
        "total_price_items falls back to two extra queries"
    )


def test_detail_response_does_not_re_aggregate(
    order_with_lines, django_assert_max_num_queries
):
    client = APIClient()
    client.force_authenticate(user=order_with_lines.user)
    url = reverse("order-detail", args=[order_with_lines.pk])

    # Generous but bounded: the point is that the count does not grow
    # with repeated reads of total_price_items inside the serializer.
    with django_assert_max_num_queries(25):
        response = client.get(url)

    assert response.status_code == 200
