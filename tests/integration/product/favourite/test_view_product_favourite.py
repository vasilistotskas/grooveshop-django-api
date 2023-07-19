from __future__ import annotations

import json

from rest_framework import status
from rest_framework.test import APITestCase

from product.models.favourite import ProductFavourite
from product.models.product import Product
from product.serializers.favourite import ProductFavouriteSerializer
from user.models import UserAccount


class ProductFavouriteViewSetTestCase(APITestCase):
    user_account: UserAccount
    product: Product
    product_favourite: ProductFavourite

    def setUp(self):
        self.user_account = UserAccount.objects.create_user(
            email="test@test.com", password="test12345@!"
        )
        self.product = Product.objects.create(
            slug="slug_one",
            price=10.00,
            active=True,
            stock=10,
            discount_percent=0.00,
            hits=0,
            weight=0.00,
        )
        self.product_favourite = ProductFavourite.objects.create(
            product=self.product,
            user=self.user_account,
        )

    def test_list(self):
        response = self.client.get("/api/v1/product/favourite/")
        product_favourites = ProductFavourite.objects.all()
        serializer = ProductFavouriteSerializer(product_favourites, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        product = Product.objects.create(
            slug="slug_two",
            price=10.00,
            active=True,
            stock=10,
            discount_percent=0.00,
            hits=0,
            weight=0.00,
        )
        payload = {
            "product": product.id,
            "user": self.user_account.id,
        }
        response = self.client.post(
            "/api/v1/product/favourite/",
            json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "product": "INVALID",
            "user": "INVALID",
        }
        response = self.client.post(
            "/api/v1/product/favourite/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(
            f"/api/v1/product/favourite/{self.product_favourite.pk}/"
        )
        product_favourite = ProductFavourite.objects.get(pk=self.product_favourite.pk)
        serializer = ProductFavouriteSerializer(product_favourite)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        response = self.client.get(
            f"/api/v1/product/favourite/{self.product_favourite.pk + 1}/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "product": self.product.id,
            "user": self.user_account.id,
        }
        response = self.client.put(
            f"/api/v1/product/favourite/{self.product_favourite.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "product": True,
            "user": True,
        }
        response = self.client.put(
            f"/api/v1/product/favourite/{self.product_favourite.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "product": self.product.id,
        }
        response = self.client.patch(
            f"/api/v1/product/favourite/{self.product_favourite.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "product": True,
        }
        response = self.client.patch(
            f"/api/v1/product/favourite/{self.product_favourite.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(
            f"/api/v1/product/favourite/{self.product_favourite.pk}/"
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        response = self.client.get(
            f"/api/v1/product/favourite/{self.product_favourite.pk + 1}/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
