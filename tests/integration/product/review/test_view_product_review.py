from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from product.models.review import ProductReview
from product.serializers.review import (
    ProductReviewDetailSerializer,
)
from user.factories.account import UserAccountFactory

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class ProductReviewViewSetTestCase(APITestCase):
    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0, is_superuser=False)
        self.admin_user = UserAccountFactory(num_addresses=0, is_superuser=True)
        self.product = ProductFactory(num_images=0, num_reviews=0)
        self.product_review = ProductReviewFactory(
            product=self.product,
            user=self.user,
            rate=5,
        )
        self.client.force_authenticate(user=self.user)

    def get_product_review_detail_url(self, pk):
        return reverse("product-review-detail", args=[pk])

    def get_product_review_list_url(self):
        return reverse("product-review-list")

    def get_user_product_review_url(self):
        return reverse("product-review-user-product-review")

    def get_product_review_product_url(self, pk):
        return reverse("product-review-product", args=[pk])

    def test_list_uses_correct_serializer(self):
        url = self.get_product_review_list_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        if response.data["results"]:
            review_data = response.data["results"][0]
            required_fields = ["id", "product", "user", "rate", "status"]
            for field in required_fields:
                self.assertIn(
                    field,
                    review_data,
                    f"Field '{field}' missing from list response",
                )

    def test_retrieve_uses_correct_serializer(self):
        url = self.get_product_review_detail_url(self.product_review.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_basic_fields = {
            "id",
            "product",
            "user",
            "rate",
            "status",
            "is_published",
            "created_at",
            "updated_at",
            "published_at",
            "uuid",
            "translations",
        }
        self.assertTrue(
            expected_basic_fields.issubset(set(response.data.keys()))
        )

    def test_create_request_response_serializers(self):
        product_2 = ProductFactory(num_images=0, num_reviews=0)
        payload = {
            "product": product_2.id,
            "rate": 4,
            "status": "NEW",
            "is_published": True,
            "translations": {
                default_language: {
                    "comment": "Great product!",
                }
            },
        }

        url = self.get_product_review_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        expected_basic_fields = {
            "id",
            "product",
            "user",
            "rate",
            "status",
            "is_published",
            "created_at",
            "updated_at",
            "uuid",
            "translations",
        }
        actual_fields = set(response.data.keys())
        self.assertTrue(expected_basic_fields.issubset(actual_fields))

        review = ProductReview.objects.get(id=response.data["id"])
        self.assertEqual(review.rate, 4)
        self.assertEqual(review.user, self.user)
        self.assertEqual(review.product.id, product_2.id)

    def test_update_request_response_serializers(self):
        payload = {
            "product": self.product.id,
            "rate": 3,
            "status": "TRUE",
            "is_published": True,
            "translations": {
                default_language: {
                    "comment": "Updated review comment",
                }
            },
        }

        url = self.get_product_review_detail_url(self.product_review.id)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_basic_fields = {
            "id",
            "product",
            "user",
            "rate",
            "status",
            "is_published",
            "created_at",
            "updated_at",
            "uuid",
            "translations",
        }
        self.assertTrue(
            expected_basic_fields.issubset(set(response.data.keys()))
        )

        review = ProductReview.objects.get(id=response.data["id"])
        self.assertEqual(review.rate, 3)
        self.assertEqual(review.status, "TRUE")

    def test_partial_update_request_response_serializers(self):
        payload = {
            "rate": 2,
            "translations": {
                default_language: {
                    "comment": "Changed my mind about this product",
                }
            },
        }

        url = self.get_product_review_detail_url(self.product_review.id)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_basic_fields = {
            "id",
            "product",
            "user",
            "rate",
            "status",
            "is_published",
            "created_at",
            "updated_at",
            "uuid",
            "translations",
        }
        self.assertTrue(
            expected_basic_fields.issubset(set(response.data.keys()))
        )

        review = ProductReview.objects.get(id=response.data["id"])
        self.assertEqual(review.rate, 2)

    def test_delete_endpoint(self):
        url = self.get_product_review_detail_url(self.product_review.id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            ProductReview.objects.filter(id=self.product_review.id).exists()
        )

    def test_filtering_functionality(self):
        url = self.get_product_review_list_url()

        response = self.client.get(url, {"product_id": self.product.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(url, {"rate": 5})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(url, {"status": "NEW"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_ordering_functionality(self):
        url = self.get_product_review_list_url()

        response = self.client.get(url, {"ordering": "created_at"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(url, {"ordering": "-rate"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_search_functionality(self):
        url = self.get_product_review_list_url()

        response = self.client.get(
            url, {"search": self.product.safe_translation_getter("name")}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_validation_errors_consistent(self):
        payload = {
            "product": 99999,
            "rate": 10,
            "status": "INVALID_STATUS",
        }

        url = self.get_product_review_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_duplicate_review_validation(self):
        payload = {
            "product": self.product.id,
            "rate": 4,
            "status": "NEW",
            "translations": {
                default_language: {
                    "comment": "Duplicate review attempt",
                }
            },
        }

        url = self.get_product_review_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "You have already reviewed this product", str(response.data)
        )

    def test_user_product_review_endpoint(self):
        url = self.get_user_product_review_url()
        payload = {"product": self.product.id}

        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.product_review.id)
        self.assertEqual(response.data["product"]["id"], self.product.id)

    def test_user_product_review_not_found(self):
        product_2 = ProductFactory(num_images=0, num_reviews=0)
        url = self.get_user_product_review_url()
        payload = {"product": product_2.id}

        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_user_product_review_unauthenticated(self):
        self.client.force_authenticate(user=None)
        url = self.get_user_product_review_url()
        payload = {"product": self.product.id}

        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_product_endpoint(self):
        url = self.get_product_review_product_url(self.product_review.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.product.id)

    def test_permissions_authenticated_actions(self):
        self.client.force_authenticate(user=None)

        url = self.get_product_review_list_url()
        response = self.client.post(url, data={}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        url = self.get_product_review_detail_url(self.product_review.id)
        response = self.client.put(url, data={}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_can_see_all_reviews(self):
        pending_review = ProductReviewFactory(
            product=self.product, user=self.admin_user, rate=3, status="NEW"
        )

        self.client.force_authenticate(user=self.admin_user)
        url = self.get_product_review_list_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        review_ids = [review["id"] for review in response.data["results"]]
        self.assertIn(pending_review.id, review_ids)

    def test_regular_user_sees_only_approved_reviews(self):
        self.product_review.status = "TRUE"
        self.product_review.save()

        other_user = UserAccountFactory(num_addresses=0, is_superuser=False)
        ProductReviewFactory(
            product=self.product, user=other_user, rate=3, status="NEW"
        )

        ProductReviewFactory(
            product=self.product, user=self.admin_user, rate=2, status="FALSE"
        )

        url = self.get_product_review_list_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        approved_reviews = [
            r for r in response.data["results"] if r["status"] == "TRUE"
        ]
        self.assertTrue(
            len(approved_reviews) >= 1,
            "Should see at least one approved review",
        )

        for review in response.data["results"]:
            if review["user"] != self.user.id:
                self.assertEqual(
                    review["status"],
                    "TRUE",
                    "Other users' non-approved reviews should not be visible",
                )

    def test_consistency_with_manual_serializer_instantiation(self):
        url = self.get_product_review_detail_url(self.product_review.id)
        viewset_response = self.client.get(url)

        serializer = ProductReviewDetailSerializer(
            self.product_review,
            context={"request": self.client.request().wsgi_request},
        )

        self.assertEqual(viewset_response.status_code, status.HTTP_200_OK)

        viewset_data = viewset_response.data
        serializer_data = serializer.data

        key_fields = ["id", "rate", "status", "uuid"]
        for field in key_fields:
            self.assertEqual(
                viewset_data[field],
                serializer_data[field],
                f"Field '{field}' differs between ViewSet and manual serializer",
            )
