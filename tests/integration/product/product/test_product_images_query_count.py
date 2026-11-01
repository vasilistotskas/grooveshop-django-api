from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from product.factories.image import ProductImageFactory
from product.factories.product import ProductFactory


class ProductImagesQueryCountTestCase(APITestCase):
    """``GET /product/<pk>/images`` must not query once per image."""

    @classmethod
    def setUpTestData(cls):
        cls.few = ProductFactory(num_images=0)
        cls.many = ProductFactory(num_images=0)
        for position, product in enumerate((cls.few, cls.many)):
            count = 2 if position == 0 else 8
            for index in range(count):
                ProductImageFactory(
                    product=product, is_main=index == 0, sort_order=index
                )

    def _queries(self, product) -> tuple[int, list[dict]]:
        url = reverse("product-images", args=[product.pk])
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return len(ctx), response.data

    def test_query_count_does_not_grow_with_images(self):
        few_queries, _ = self._queries(self.few)
        many_queries, _ = self._queries(self.many)

        self.assertLessEqual(abs(many_queries - few_queries), 1)

    def test_response_keeps_sort_order(self):
        _, data = self._queries(self.many)

        self.assertEqual([row["sort_order"] for row in data], list(range(8)))
