"""An export of a changelist queryset reads whole rows in one query.

``ProductAdmin``'s changelist loads only the columns it lists
(``.only()``), and an admin action receives that queryset. The export
writes every field, so it read each deferred column with a query of its
own, per row. ``ExportActionMixin`` now clears the deferral first.
"""

from __future__ import annotations

import pytest
from django.contrib.admin import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from product.admin import ProductAdmin
from product.factories.product import ProductFactory
from product.models.product import Product
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _export_queries(rows: int) -> int:
    Product.objects.all().delete()
    ProductFactory.create_batch(rows, num_images=0, num_reviews=0)
    request = RequestFactory().get("/admin/product/product/")
    request.user = UserAccountFactory(is_staff=True, is_superuser=True)
    request.session = {}
    request._messages = FallbackStorage(request)
    model_admin = ProductAdmin(Product, AdminSite())
    slim = Product.objects.only("id", "sku", "price", "price_currency")

    with CaptureQueriesContext(connection) as captured:
        response = model_admin.export_csv(request, slim)

    assert response.status_code == 200
    return len(captured)


def test_the_export_query_count_does_not_grow_with_the_rows():
    assert _export_queries(2) == _export_queries(5)
