from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from product.models.product import Product
from product.models.review import ProductReview
from product.serializers.review import ProductReviewSerializer
from user.factories.account import UserAccountFactory

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class ProductReviewViewSetTestCase(APITestCase):
    user: User = None
    product: Product = None
    product_review: ProductReview = None

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0, is_superuser=True)
        self.client.force_authenticate(user=self.user)
        self.product = ProductFactory(num_images=0, num_reviews=0)
        self.product_review = ProductReviewFactory(
            product=self.product,
            user=self.user,
            rate=5,
        )

    @staticmethod
    def get_product_review_detail_url(pk):
        return reverse("product-review-detail", args=[pk])

    @staticmethod
    def get_product_review_list_url():
        return reverse("product-review-list")

    def test_list(self):
        url = self.get_product_review_list_url()
        response = self.client.get(url)
        reviews = ProductReview.objects.all()
        serializer = ProductReviewSerializer(reviews, many=True)

        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        product_2 = ProductFactory(num_images=0, num_reviews=0)
        payload = {
            "product": product_2.pk,
            "user": self.user.pk,
            "rate": 5,
            "status": "NEW",
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "comment": f"New Review comment in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_product_review_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "product": "invalid_product",
            "user": "invalid_user",
            "rate": "invalid_rate",
            "status": "invalid_status",
            "translations": {
                "invalid_language": {
                    "comment": "invalid_comment",
                }
            },
        }

        url = self.get_product_review_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_product_review_detail_url(self.product_review.pk)
        response = self.client.get(url)
        review = ProductReview.objects.get(pk=self.product_review.pk)
        serializer = ProductReviewSerializer(review)

        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_product_review_pk = 999999
        url = self.get_product_review_detail_url(invalid_product_review_pk)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "product": self.product.pk,
            "user": self.user.pk,
            "rate": 1,
            "status": "NEW",
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "comment": f"Updated Review comment in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_product_review_detail_url(self.product_review.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "product": "invalid_product",
            "user": "invalid_user",
            "rate": "invalid_rate",
            "status": "invalid_status",
            "translations": {
                "invalid_language": {
                    "comment": "invalid_comment",
                }
            },
        }

        url = self.get_product_review_detail_url(self.product_review.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "rate": 1,
            "translations": {
                default_language: {
                    "comment": "Updated Review comment",
                }
            },
        }

        url = self.get_product_review_detail_url(self.product_review.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "rate": "invalid_rate",
            "translations": {
                "invalid_language": {
                    "comment": "invalid_comment",
                }
            },
        }

        url = self.get_product_review_detail_url(self.product_review.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_product_review_detail_url(self.product_review.pk)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ProductReview.objects.filter(pk=self.product_review.pk).exists())

    def test_destroy_invalid(self):
        invalid_product_review_pk = 999999
        url = self.get_product_review_detail_url(invalid_product_review_pk)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def tearDown(self) -> None:
        Product.objects.all().delete()
        ProductReview.objects.all().delete()
        User.objects.all().delete()
        super().tearDown()
