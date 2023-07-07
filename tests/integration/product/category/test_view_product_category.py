from __future__ import annotations

import json

from product.models.category import ProductCategory
from product.serializers.category import ProductCategorySerializer
from rest_framework import status
from rest_framework.test import APITestCase


class ProductCategoryViewSetTestCase(APITestCase):
    product_category: ProductCategory

    def setUp(self):
        self.product_category = ProductCategory.objects.create(
            name="test",
            slug="test",
            description="test",
        )

    def test_list(self):
        response = self.client.get("/api/v1/product/category/")
        product_categories = ProductCategory.objects.all()
        serializer = ProductCategorySerializer(product_categories, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "name": "test_one",
            "slug": "test_one",
            "description": "test_one",
        }
        response = self.client.post(
            "/api/v1/product/category/",
            json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {}
        response = self.client.post(
            "/api/v1/product/category/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(
            f"/api/v1/product/category/{self.product_category.pk}/"
        )
        product_category = ProductCategory.objects.get(pk=self.product_category.pk)
        serializer = ProductCategorySerializer(product_category)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        response = self.client.get(
            f"/api/v1/product/category/{self.product_category.pk + 1}/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "name": "test_two",
            "slug": "test_two",
            "description": "test_two",
        }
        response = self.client.put(
            f"/api/v1/product/category/{self.product_category.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "name": "test_two",
            "slug": "test_two",
            "description": "test_two",
            "parent": False,
        }
        response = self.client.put(
            f"/api/v1/product/category/{self.product_category.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "name": "test_three",
        }
        response = self.client.patch(
            f"/api/v1/product/category/{self.product_category.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "parent": 0,
        }
        response = self.client.patch(
            f"/api/v1/product/category/{self.product_category.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(
            f"/api/v1/product/category/{self.product_category.pk}/"
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        response = self.client.get(
            f"/api/v1/product/category/{self.product_category.pk + 1}/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
