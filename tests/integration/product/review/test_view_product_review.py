from __future__ import annotations

import json

from django.conf import settings
from rest_framework import status
from rest_framework.test import APITestCase

from product.models.product import Product
from product.models.review import ProductReview
from product.serializers.review import ProductReviewSerializer
from user.models import UserAccount


class ProductReviewViewSetTestCase(APITestCase):
    user_account: UserAccount
    product: Product
    product_review: ProductReview

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
        self.product_review = ProductReview.objects.create(
            product=self.product,
            user=self.user_account,
            rate=1,
            status="True",
        )

        self.client.force_authenticate(user=self.user_account)

    def test_list(self):
        response = self.client.get("/api/v1/product/review/")
        product_reviews = ProductReview.objects.all()
        serializer = ProductReviewSerializer(product_reviews, many=True)
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
            "translations": {},
            "product": product.id,
            "user": self.user_account.id,
            "rate": 1,
            "status": "True",
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "comment": f"Translation for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.post(
            "/api/v1/product/review/",
            json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "product": "INVALID",
            "user": "INVALID",
            "rate": False,
            "status": 12345,
        }
        response = self.client.post(
            "/api/v1/product/review/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(f"/api/v1/product/review/{self.product_review.pk}/")
        product_review = ProductReview.objects.get(pk=self.product_review.pk)
        serializer = ProductReviewSerializer(product_review)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        response = self.client.get(
            f"/api/v1/product/review/{self.product_review.pk + 1}/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "translations": {},
            "product": self.product.id,
            "user": self.user_account.id,
            "rate": 5,
            "status": "True",
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "comment": f"Translation for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.put(
            f"/api/v1/product/review/{self.product_review.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "product": "123",
            "user": "123",
            "rate": False,
            "status": 12345,
        }
        response = self.client.put(
            f"/api/v1/product/review/{self.product_review.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "status": "False",
        }
        response = self.client.patch(
            f"/api/v1/product/review/{self.product_review.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "product": "123",
            "user": "123",
            "rate": False,
            "status": 12345,
        }
        response = self.client.patch(
            f"/api/v1/product/review/{self.product_review.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(
            f"/api/v1/product/review/{self.product_review.pk}/"
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        response = self.client.get(
            f"/api/v1/product/review/{self.product_review.pk + 1}/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
