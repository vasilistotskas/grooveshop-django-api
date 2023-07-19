from __future__ import annotations

import json

from django.conf import settings
from rest_framework import status
from rest_framework.test import APITestCase

from product.models.category import ProductCategory
from product.serializers.category import ProductCategorySerializer


class ProductCategoryViewSetTestCase(APITestCase):
    product_category: ProductCategory

    def setUp(self):
        self.product_category = ProductCategory.objects.create(
            slug="slug_one",
        )

    def test_list(self):
        response = self.client.get("/api/v1/product/category/")
        product_categories = ProductCategory.objects.all()
        serializer = ProductCategorySerializer(product_categories, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "translations": {},
            "slug": "test_one",
            "description": "test_one",
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Translation for {language_name}",
                "description": f"Translation for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

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
            "translations": {},
            "slug": "test_two",
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Translation for {language_name}",
                "description": f"Translation for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.put(
            f"/api/v1/product/category/{self.product_category.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
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
            "slug": "test_three",
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
