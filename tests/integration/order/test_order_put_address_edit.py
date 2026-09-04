"""A full PUT must still work when a line has sold out.

``OrderWriteSerializer`` is wired to update/partial_update only, and
``update()`` deliberately discards ``items`` — a line carries a committed
stock movement and a price snapshot, so changing what was ordered goes
through the refund/cancel flows instead.

The field was nonetheless required and stock-validated, so a PUT carrying
the representation the API itself returns was rejected whenever any
product on the order had since sold out. The owner could not correct
their own delivery address until the shop restocked something unrelated.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from product.factories.product import ProductFactory
from product.models.product import Product
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _put_body(order):
    return {
        "user": order.user_id,
        "country": order.country_id,
        "region": order.region_id,
        "floor": order.floor,
        "location_type": order.location_type,
        "street": order.street,
        "street_number": order.street_number,
        "pay_way": order.pay_way_id,
        "status": order.status,
        "first_name": order.first_name,
        "last_name": order.last_name,
        "email": order.email,
        "zipcode": order.zipcode,
        "place": order.place,
        "city": "Thessaloniki",
        # E.164 — the factory's stored form does not round-trip.
        "phone": "+306912345678",
        "customer_notes": order.customer_notes,
    }


def test_address_edit_survives_a_sold_out_line():
    user = UserAccountFactory(num_addresses=0)
    order = OrderFactory(user=user, num_order_items=0)
    product = ProductFactory(num_images=0, num_reviews=0, stock=5)
    OrderItemFactory(order=order, product=product, quantity=2)

    # The product sells out after the order was placed.
    Product.objects.filter(pk=product.pk).update(stock=0)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.put(
        reverse("order-detail", args=[order.pk]),
        _put_body(order),
        format="json",
    )

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert order.city == "Thessaloniki"


def test_the_lines_are_untouched_by_the_edit():
    user = UserAccountFactory(num_addresses=0)
    order = OrderFactory(user=user, num_order_items=0)
    product = ProductFactory(num_images=0, num_reviews=0, stock=5)
    OrderItemFactory(order=order, product=product, quantity=2)

    client = APIClient()
    client.force_authenticate(user=user)
    body = _put_body(order)
    body["items"] = []
    client.put(reverse("order-detail", args=[order.pk]), body, format="json")

    order.refresh_from_db()
    assert order.items.count() == 1
    assert order.items.first().quantity == 2
