"""Recursive product counts on the category list endpoints."""

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from product.factories.category import ProductCategoryFactory
from product.factories.product import ProductFactory
from product.models.category import ProductCategory


def _product(category, **kwargs):
    return ProductFactory(
        category=category, num_images=0, num_reviews=0, **kwargs
    )


class CategoryProductCountTestCase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.parent = ProductCategoryFactory(parent=None)
        cls.child = ProductCategoryFactory(parent=cls.parent)
        cls.grandchild = ProductCategoryFactory(parent=cls.child)
        cls.sibling = ProductCategoryFactory(parent=None)

        _product(cls.parent, active=True)
        _product(cls.child, active=True)
        _product(cls.grandchild, active=True)
        _product(cls.grandchild, active=True)
        # Never counted: not on sale, soft-deleted.
        _product(cls.grandchild, active=False)
        _product(cls.grandchild, active=True, is_deleted=True)
        _product(cls.sibling, active=True)

    def _counts(self, name="product-category-list"):
        response = self.client.get(reverse(name))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        rows = data["results"] if isinstance(data, dict) else data
        return {row["id"]: row["recursive_product_count"] for row in rows}

    def test_parent_counts_its_descendants_products(self):
        counts = self._counts()

        self.assertEqual(counts[self.parent.id], 4)
        self.assertEqual(counts[self.child.id], 3)
        self.assertEqual(counts[self.grandchild.id], 2)
        self.assertEqual(counts[self.sibling.id], 1)

    def test_all_endpoint_carries_the_same_counts(self):
        self.assertEqual(self._counts("product-category-all"), self._counts())

    def test_detail_shares_the_definition(self):
        response = self.client.get(
            reverse("product-category-detail", args=[self.parent.id])
        )

        self.assertEqual(response.data["recursive_product_count"], 4)

    def test_bare_instance_falls_back_to_the_same_value(self):
        bare = ProductCategory.objects.get(pk=self.parent.pk)

        self.assertEqual(bare.recursive_product_count, 4)

    def test_list_query_count_is_flat_in_category_count(self):
        url = reverse("product-category-list")

        def count_queries():
            self.client.get(url)  # prime caches the factories clear
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(url)
            return len(ctx)

        before = count_queries()
        for _ in range(3):
            _product(ProductCategoryFactory(parent=self.sibling), active=True)

        self.assertEqual(count_queries(), before)
