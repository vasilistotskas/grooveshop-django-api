from __future__ import annotations

import json

from django.conf import settings
from rest_framework import status
from rest_framework.test import APITestCase

from product.models.category import ProductCategory
from product.models.product import Product
from product.serializers.product import ProductSerializer
from vat.models import Vat


class ProductViewSetTestCase(APITestCase):
    product: Product

    def setUp(self):
        self.product = Product.objects.create(
            slug="slug_one",
            price=10.00,
            active=True,
            stock=10,
            discount_percent=0.00,
            hits=0,
            weight=0.00,
        )

    def test_list(self):
        response = self.client.get("/api/v1/product/")
        products = Product.objects.all()
        serializer = ProductSerializer(products, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        category = ProductCategory.objects.create(
            slug="slug_two",
        )
        vat = Vat.objects.create(value=20)

        payload = {
            "translations": {},
            "slug": "slug_three",
            "price": 10.00,
            "active": True,
            "stock": 10,
            "discount_percent": 0.00,
            "hits": 0,
            "weight": 0.00,
            "category": category.pk,
            "vat": vat.pk,
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
            "/api/v1/product/", json.dumps(payload), content_type="application/json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "slug": True,
            "price": "invalid",
            "active": "invalid",
            "stock": "invalid",
            "discount_percent": "invalid",
            "hits": "invalid",
            "weight": "invalid",
        }
        response = self.client.post(
            "/api/v1/product/", json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(f"/api/v1/product/{self.product.pk}/")
        product = Product.objects.get(pk=self.product.pk)
        serializer = ProductSerializer(product)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        response = self.client.get(f"/api/v1/product/{self.product.pk + 1}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        category = ProductCategory.objects.create(
            slug="slug_four",
        )
        vat = Vat.objects.create(value=25)
        payload = {
            "translations": {},
            "slug": "slug_five",
            "price": 10.00,
            "active": True,
            "stock": 10,
            "discount_percent": 0.00,
            "hits": 0,
            "weight": 0.00,
            "category": category.pk,
            "vat": vat.pk,
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
            f"/api/v1/product/{self.product.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "slug": True,
            "price": "invalid",
            "active": "invalid",
            "stock": "invalid",
            "discount_percent": "invalid",
            "hits": "invalid",
            "weight": "invalid",
        }
        response = self.client.put(
            f"/api/v1/product/{self.product.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "slug": "slug_six",
            "price": 10.00,
            "active": True,
            "stock": 10,
            "discount_percent": 0.00,
            "hits": 0,
            "weight": 0.00,
        }
        response = self.client.patch(
            f"/api/v1/product/{self.product.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "slug": True,
            "price": "invalid",
            "active": "invalid",
            "stock": "invalid",
            "discount_percent": "invalid",
            "hits": "invalid",
            "weight": "invalid",
        }
        response = self.client.patch(
            f"/api/v1/product/{self.product.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(f"/api/v1/product/{self.product.pk}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        response = self.client.get(f"/api/v1/product/{self.product.pk + 1}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
