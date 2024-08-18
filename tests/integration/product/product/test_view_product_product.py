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
from product.serializers.product import ProductSerializer
from user.factories.account import UserAccountFactory
from vat.factories import VatFactory
from vat.models import Vat


languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
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
        serializer = ProductSerializer(products, many=True)

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
        serializer = ProductSerializer(product)

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

    def tearDown(self) -> None:
        Product.objects.all().delete()
        ProductCategory.objects.all().delete()
        ProductImage.objects.all().delete()
        ProductReview.objects.all().delete()
        ProductFavourite.objects.all().delete()
        Vat.objects.all().delete()
        User.objects.all().delete()
        super().tearDown()
