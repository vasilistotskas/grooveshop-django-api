from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.pagination.limit_offset import LimitOffsetPaginator
from product.factories.category import ProductCategoryFactory
from product.factories.favourite import ProductFavouriteFactory
from product.factories.image import ProductImageFactory
from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from product.models.category import ProductCategory
from product.models.favourite import ProductFavourite
from product.models.image import ProductImage
from product.models.product import Product
from product.models.review import ProductReview
from product.serializers.product import (
    ProductDetailSerializer,
    ProductListSerializer,
)
from user.factories.account import UserAccountFactory
from vat.factories import VatFactory
from vat.models import Vat

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class ProductViewSetTestCase(APITestCase):
    product: Product = None
    user: User = None
    category: ProductCategory = None
    vat: Vat = None
    images: list[ProductImage] = []
    reviews: list[ProductReview] = []
    favourite: ProductFavourite = None

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.category = ProductCategoryFactory()
        self.vat = VatFactory()
        self.product = ProductFactory(
            category=self.category,
            vat=self.vat,
        )
        main_product_image = ProductImageFactory(
            product=self.product,
            is_main=True,
        )

        self.images.append(main_product_image)

        non_main_product_image = ProductImageFactory(
            product=self.product,
            is_main=False,
        )

        self.images.append(non_main_product_image)

        self.favourite = ProductFavouriteFactory(
            product=self.product,
            user=self.user,
        )

        user_2 = UserAccountFactory(num_addresses=0)

        product_review_status_true = ProductReviewFactory(
            product=self.product,
            user=self.user,
            status="True",
        )
        self.reviews.append(product_review_status_true)

        product_review_status_false = ProductReviewFactory(
            product=self.product,
            user=user_2,
            status="False",
        )
        self.reviews.append(product_review_status_false)

    @staticmethod
    def get_product_detail_url(pk):
        return reverse("product-detail", args=[pk])

    @staticmethod
    def get_product_list_url():
        return reverse("product-list")

    def test_list(self):
        url = self.get_product_list_url()
        response = self.client.get(url)
        pagination = LimitOffsetPaginator()
        limit = pagination.default_limit
        products = Product.objects.all()[0:limit]
        serializer = ProductListSerializer(
            products, many=True, context={"request": response.wsgi_request}
        )

        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "slug": "sample-product-2",
            "product_code": "P123456",
            "category": self.category.pk,
            "translations": {},
            "price": "100.00",
            "active": True,
            "stock": 10,
            "discount_percent": "50.0",
            "vat": self.vat.pk,
            "view_count": 10,
            "weight": {
                "value": "5.00",
                "unit": "kg",
            },
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"New Product name in {language_name}",
                "description": f"New Product description in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_product_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "product_code": "invalid_product_code",
            "category": "invalid_category",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                    "description": "Translation for invalid language code",
                }
            },
            "price": "invalid_price",
            "active": "invalid_active",
            "stock": "invalid_stock",
            "discount_percent": "invalid_discount_percent",
            "vat": "invalid_vat",
            "view_count": "invalid_view_count",
            "weight": "invalid_weight",
        }

        url = self.get_product_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_product_detail_url(self.product.pk)
        response = self.client.get(url)
        product = Product.objects.get(pk=self.product.pk)
        serializer = ProductDetailSerializer(
            product, context={"request": response.wsgi_request}
        )

        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_product_id = 999999999
        url = self.get_product_detail_url(invalid_product_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "slug": "sample-product-2",
            "product_code": "P123456",
            "category": self.category.pk,
            "translations": {},
            "price": "100.00",
            "active": True,
            "stock": 10,
            "discount_percent": "50.0",
            "vat": self.vat.pk,
            "view_count": 10,
            "weight": {
                "value": "5.00",
                "unit": "kg",
            },
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Updated Product name in {language_name}",
                "description": f"Updated Product description in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_product_detail_url(self.product.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "product_code": "invalid_product_code",
            "category": "invalid_category",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                    "description": "Translation for invalid language code",
                }
            },
            "price": "invalid_price",
            "active": "invalid_active",
            "stock": "invalid_stock",
            "discount_percent": "invalid_discount_percent",
            "vat": "invalid_vat",
            "hits": "invalid_view_count",
            "weight": {
                "value": "5.00",
                "unit": "kg",
            },
        }

        url = self.get_product_detail_url(self.product.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "translations": {
                default_language: {
                    "name": "Updated Product name",
                    "description": "Updated Product description",
                }
            },
        }

        url = self.get_product_detail_url(self.product.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "product_code": "invalid_product_code",
            "category": "invalid_category",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                    "description": "Translation for invalid language code",
                }
            },
            "price": "invalid_price",
            "weight": "invalid_weight",
        }

        url = self.get_product_detail_url(self.product.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_product_detail_url(self.product.pk)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())

    def test_destroy_invalid(self):
        invalid_product_id = 999999999
        url = self.get_product_detail_url(invalid_product_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_ordering_by_discount_value(self):
        ProductFactory(price=200, discount_percent=5)
        ProductFactory(price=100, discount_percent=20)

        url = f"{self.get_product_list_url()}?ordering=discountValueAmount"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]

        discount_values = [product["discount_value"] for product in results]
        self.assertEqual(sorted(discount_values), discount_values)

        url = f"{self.get_product_list_url()}?ordering=-discountValueAmount"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]

        discount_values = [product["discount_value"] for product in results]
        self.assertEqual(sorted(discount_values, reverse=True), discount_values)

    def test_ordering_by_final_price(self):
        ProductFactory(price=150, discount_percent=10)
        ProductFactory(price=200, discount_percent=5)

        url = f"{self.get_product_list_url()}?ordering=finalPriceAmount"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]

        final_prices = [product["final_price"] for product in results]
        self.assertEqual(sorted(final_prices), final_prices)

        url = f"{self.get_product_list_url()}?ordering=-finalPriceAmount"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]

        final_prices = [product["final_price"] for product in results]
        self.assertEqual(sorted(final_prices, reverse=True), final_prices)

    def test_ordering_by_review_average(self):
        product2 = ProductFactory()
        product3 = ProductFactory()

        ProductReviewFactory(product=product2, rate=3, status="True")
        ProductReviewFactory(product=product2, rate=4, status="True")
        ProductReviewFactory(product=product3, rate=5, status="True")

        url = f"{self.get_product_list_url()}?ordering=reviewAverageField"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]

        review_averages = [product["review_average"] for product in results]
        self.assertEqual(sorted(review_averages), review_averages)

        url = f"{self.get_product_list_url()}?ordering=-reviewAverageField"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]

        review_averages = [product["review_average"] for product in results]
        self.assertEqual(sorted(review_averages, reverse=True), review_averages)

    def test_ordering_by_likes_count(self):
        product2 = ProductFactory()
        product3 = ProductFactory()

        user2 = UserAccountFactory(num_addresses=0)
        user3 = UserAccountFactory(num_addresses=0)

        ProductFavouriteFactory(product=product2, user=user2)

        ProductFavouriteFactory(product=product3, user=user2)
        ProductFavouriteFactory(product=product3, user=user3)

        url = f"{self.get_product_list_url()}?ordering=likesCountField"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]

        likes_counts = [product["likes_count"] for product in results]
        self.assertEqual(sorted(likes_counts), likes_counts)

        url = f"{self.get_product_list_url()}?ordering=-likesCountField"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]

        likes_counts = [product["likes_count"] for product in results]
        self.assertEqual(sorted(likes_counts, reverse=True), likes_counts)

    def test_filter_by_price_range(self):
        ProductFactory(price=150, discount_percent=0)
        ProductFactory(price=250, discount_percent=0)

        url = f"{self.get_product_list_url()}?min_final_price=200"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]

        for product in results:
            self.assertGreaterEqual(float(product["final_price"]), 200)

        url = f"{self.get_product_list_url()}?max_final_price=200"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]

        for product in results:
            self.assertLessEqual(float(product["final_price"]), 200)

        url = f"{self.get_product_list_url()}?min_final_price=100&max_final_price=200"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]

        for product in results:
            price = float(product["final_price"])
            self.assertGreaterEqual(price, 100)
            self.assertLessEqual(price, 200)
