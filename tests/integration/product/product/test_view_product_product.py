from decimal import Decimal
from typing import List

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.pagination.limit_offset import LimitOffsetPaginator
from helpers.seed import get_or_create_default_image
from product.models.category import ProductCategory
from product.models.favourite import ProductFavourite
from product.models.image import ProductImage
from product.models.product import Product
from product.models.review import ProductReview
from product.serializers.product import ProductSerializer
from vat.models import Vat


languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.memory.InMemoryStorage",
        },
    }
)
class ProductViewSetTestCase(APITestCase):
    product: Product = None
    user: User = None
    category: ProductCategory = None
    vat: Vat = None
    product_images: List[ProductImage] = []
    product_reviews: List[ProductReview] = []
    product_favourite: ProductFavourite = None

    def setUp(self):
        self.user = User.objects.create_user(
            email="test@test.com", password="test12345@!"
        )
        self.category = ProductCategory.objects.create(
            slug="sample-category",
        )
        for language in languages:
            self.category.set_current_language(language)
            self.category.name = f"Sample Category ({language})"
            self.category.description = (
                f"This is a sample category description ({language})."
            )
            self.category.save()
        self.category.set_current_language(default_language)

        self.vat = Vat.objects.create(
            value=Decimal("24.0"),
        )

        self.product = Product.objects.create(
            product_code="P123456",
            category=self.category,
            slug="sample-product",
            price=Decimal("100.00"),
            active=True,
            stock=10,
            discount_percent=Decimal("50.0"),
            vat=self.vat,
            view_count=10,
            weight=Decimal("5.00"),
        )
        for language in languages:
            self.product.set_current_language(language)
            self.product.name = f"Sample Product ({language})"
            self.product.description = (
                f"This is a sample product description ({language})."
            )
            self.product.save()
        self.product.set_current_language(default_language)

        image = get_or_create_default_image("uploads/products/no_photo.jpg")
        main_product_image = ProductImage.objects.create(
            product=self.product,
            image=image,
            is_main=True,
        )
        for language in languages:
            main_product_image.set_current_language(language)
            main_product_image.title = f"Sample Main Product Image ({language})"
            main_product_image.save()
        main_product_image.set_current_language(default_language)
        self.product_images.append(main_product_image)

        non_main_product_image = ProductImage.objects.create(
            product=self.product,
            image=image,
            is_main=False,
        )
        for language in languages:
            non_main_product_image.set_current_language(language)
            non_main_product_image.title = f"Sample Non-Main Product Image ({language})"
            non_main_product_image.save()
        non_main_product_image.set_current_language(default_language)
        self.product_images.append(non_main_product_image)

        self.product_favourite = ProductFavourite.objects.create(
            product=self.product,
            user=self.user,
        )

        user_2 = User.objects.create_user(
            email="test2@test.com", password="test12345@!"
        )

        product_review_status_true = ProductReview.objects.create(
            product=self.product,
            user=self.user,
            rate=5,
            status="True",
            comment="Sample Product Review Comment",
        )
        self.product_reviews.append(product_review_status_true)

        product_review_status_false = ProductReview.objects.create(
            product=self.product,
            user=user_2,
            rate=5,
            status="False",
            comment="Sample Product Review Comment",
        )
        self.product_reviews.append(product_review_status_false)

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
        super().tearDown()
        self.product_favourite.delete()
        self.product.delete()
        self.user.delete()
        self.category.delete()
        self.vat.delete()
