"""What the product changelist reads.

``ProductAdmin.get_queryset`` used to prefetch every row's active stock
reservations into an attribute nothing read: one query on every list load.
The reservation summary of the change page and the reservation filter read
the table themselves, so the list must not.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from order.models.stock_reservation import StockReservation
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def test_the_changelist_does_not_read_stock_reservations():
    for _ in range(2):
        ProductFactory(num_images=0, num_reviews=0)
    client = Client()
    client.force_login(
        UserAccountFactory(admin=True),
        backend="tenant.auth_backends.PlatformStaffBackend",
    )
    url = reverse("admin:product_product_changelist")
    # The first render re-fills what the new rows invalidated (the
    # sidebar's cached counts); only the second is read.
    client.get(url)

    with CaptureQueriesContext(connection) as queries:
        assert client.get(url).status_code == 200

    table = StockReservation._meta.db_table
    reading = [q["sql"] for q in queries.captured_queries if table in q["sql"]]
    assert reading == []
