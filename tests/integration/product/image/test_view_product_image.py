import os

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.utils.serializers import flatten_dict_for_form_data
from core.utils.testing import TestURLFixerMixin
from product.factories.image import ProductImageFactory
from product.factories.product import ProductFactory
from product.models.image import ProductImage
from product.models.product import Product

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class ProductImageViewSetTestCase(TestURLFixerMixin, APITestCase):
    product: Product = None
    product_image: ProductImage = None
    default_image: SimpleUploadedFile = None

    def setUp(self):
        self.product = ProductFactory(num_images=0, num_reviews=0)
        self.product_image = ProductImageFactory(
            product=self.product,
            is_main=True,
        )

    @staticmethod
    def get_product_image_detail_url(pk):
        return reverse("product-image-detail", args=[pk])

    @staticmethod
    def get_product_image_list_url():
        return reverse("product-image-list")

    def _create_mock_image(self):
        image_path = os.path.join(settings.STATIC_ROOT, "images", "default.png")

        if not os.path.exists(image_path):
            image_path = os.path.join(
                settings.BASE_DIR, "static", "images", "default.png"
            )

        with open(image_path, "rb") as f:
            image_data = f.read()

        image_file = SimpleUploadedFile(
            name="test_image.png", content=image_data, content_type="image/png"
        )
        return image_file

    def test_list(self):
        url = self.get_product_image_list_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        if isinstance(response.data, dict) and "results" in response.data:
            images = ProductImage.objects.all()
            self.assertEqual(len(response.data["results"]), images.count())
        else:
            images = ProductImage.objects.all()
            self.assertEqual(len(response.data), images.count())

    def test_create_valid(self):
        payload = {
            "product": self.product.id,
            "image": self._create_mock_image(),
            "is_main": False,
            "translations": {
                default_language: {
                    "title": "Product Image",
                },
            },
        }

        payload = flatten_dict_for_form_data(payload)
        url = self.get_product_image_list_url()
        response = self.client.post(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "product": "invalid_product_id",
            "image": "invalid_image_id",
            "is_main": "invalid_is_main",
            "translations": {
                "invalid_lang_code": {
                    "title": "Translation for invalid language code",
                },
            },
        }

        url = self.get_product_image_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_product_image_detail_url(self.product_image.id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.product_image.refresh_from_db()

        self.assertIn("id", response.data)
        self.assertEqual(response.data["id"], self.product_image.id)

        self.assertIn("product", response.data)
        self.assertIsNotNone(response.data["product"])

        self.assertIn("is_main", response.data)
        self.assertEqual(response.data["is_main"], self.product_image.is_main)

    def test_retrieve_invalid(self):
        invalid_product_image_id = 999999
        url = self.get_product_image_detail_url(invalid_product_image_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "product": self.product.id,
            "image": self._create_mock_image(),
            "is_main": False,
            "translations": {
                default_language: {
                    "title": "Product Image",
                },
            },
        }

        payload = flatten_dict_for_form_data(payload)
        url = self.get_product_image_detail_url(self.product_image.id)
        response = self.client.put(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "product": "invalid_product_id",
            "image": "invalid_image_id",
            "is_main": "invalid_is_main",
            "translations": {
                "invalid_lang_code": {
                    "title": "Translation for invalid language code",
                },
            },
        }

        url = self.get_product_image_detail_url(self.product_image.id)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "is_main": False,
        }

        url = self.get_product_image_detail_url(self.product_image.id)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "is_main": "invalid_is_main",
        }

        url = self.get_product_image_detail_url(self.product_image.id)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_product_image_detail_url(self.product_image.id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            ProductImage.objects.filter(pk=self.product_image.id).exists()
        )

    def test_destroy_invalid(self):
        invalid_product_image_id = 999999
        url = self.get_product_image_detail_url(invalid_product_image_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
