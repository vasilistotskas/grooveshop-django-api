import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.utils.serializers import flatten_dict_for_form_data
from product.factories.category import ProductCategoryFactory
from product.models.category import ProductCategory
from product.serializers.category import ProductCategorySerializer

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class ProductCategoryViewSetTestCase(APITestCase):
    category: ProductCategory = None
    sub_category: ProductCategory = None

    def setUp(self):
        self.category = ProductCategoryFactory()
        self.sub_category = ProductCategoryFactory(
            parent=self.category,
        )

    @staticmethod
    def get_product_category_detail_url(pk):
        return reverse("product-category-detail", args=[pk])

    @staticmethod
    def get_product_category_list_url():
        return reverse("product-category-list")

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
        url = self.get_product_category_list_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "slug": "new-category",
            "parent": self.category.id,
            "menu_image_one": self._create_mock_image(),
            "menu_image_two": self._create_mock_image(),
            "menu_main_banner": self._create_mock_image(),
            "translations": {
                default_language: {
                    "name": "New Category",
                    "description": "New Category Description",
                },
            },
        }

        payload = flatten_dict_for_form_data(payload)

        url = self.get_product_category_list_url()
        response = self.client.post(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        category = ProductCategory.objects.get(id=response.data["id"])
        serializer = ProductCategorySerializer(category)

        self.assertEqual(response.data, serializer.data)

    def test_create_invalid(self):
        payload = {
            "slug": "invalid_category_slug",
            "parent": "invalid_parent_id",
            "menu_image_one": "invalid_image_id",
            "menu_image_two": "invalid_image_id",
            "menu_main_banner": "invalid_image_id",
            "translations": {
                "invalid_lang_code": {
                    "name": "Invalid Category Name",
                    "description": "Invalid Category Description",
                },
            },
        }

        url = self.get_product_category_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_product_category_detail_url(self.category.id)
        response = self.client.get(url)
        category = ProductCategory.objects.get(pk=self.category.id)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("id", response.data)
        self.assertEqual(response.data["id"], category.id)

        self.assertIn("translations", response.data)
        self.assertIsInstance(response.data["translations"], dict)

        for lang_data in response.data["translations"].values():
            self.assertIn("name", lang_data)
            self.assertIsInstance(lang_data["name"], str)
            self.assertIn("description", lang_data)
            self.assertIsInstance(lang_data["description"], str)

        if category.parent:
            self.assertIn("parent", response.data)
            self.assertEqual(response.data["parent"], category.parent.id)

    def test_retrieve_invalid(self):
        invalid_category_id = 999999
        url = self.get_product_category_detail_url(invalid_category_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "slug": "updated-category",
            "menu_image_one": self._create_mock_image(),
            "menu_image_two": self._create_mock_image(),
            "menu_main_banner": self._create_mock_image(),
            "translations": {
                default_language: {
                    "name": "Updated Category",
                    "description": "Updated Category Description",
                },
            },
        }

        payload = flatten_dict_for_form_data(payload)

        url = self.get_product_category_detail_url(self.category.id)
        response = self.client.put(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        category = ProductCategory.objects.get(id=response.data["id"])
        serializer = ProductCategorySerializer(category)

        self.assertEqual(response.data, serializer.data)

    def test_update_invalid(self):
        payload = {
            "slug": "invalid_category_slug",
            "parent": "invalid_parent_id",
            "menu_image_one": "invalid_image_id",
            "menu_image_two": "invalid_image_id",
            "menu_main_banner": "invalid_image_id",
            "translations": {
                "invalid_lang_code": {
                    "name": "Invalid Category Name",
                    "description": "Invalid Category Description",
                },
            },
        }

        url = self.get_product_category_detail_url(self.category.id)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "slug": "updated-category",
            "menu_image_one": self._create_mock_image(),
            "menu_image_two": self._create_mock_image(),
            "menu_main_banner": self._create_mock_image(),
            "translations": {
                default_language: {
                    "name": "Updated Category",
                    "description": "Updated Category Description",
                },
            },
        }

        payload = flatten_dict_for_form_data(payload)

        url = self.get_product_category_detail_url(self.category.id)
        response = self.client.patch(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        category = ProductCategory.objects.get(id=response.data["id"])
        serializer = ProductCategorySerializer(category)

        self.assertEqual(response.data, serializer.data)

    def test_partial_update_invalid(self):
        payload = {
            "slug": "invalid_category_slug",
            "parent": "invalid_parent_id",
            "menu_image_one": "invalid_image_id",
            "menu_image_two": "invalid_image_id",
            "menu_main_banner": "invalid_image_id",
            "translations": {
                "invalid_lang_code": {
                    "name": "Invalid Category Name",
                    "description": "Invalid Category Description",
                },
            },
        }

        url = self.get_product_category_detail_url(self.category.id)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_product_category_detail_url(self.category.id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            ProductCategory.objects.filter(id=self.category.id).exists()
        )

    def test_destroy_invalid(self):
        invalid_category_id = 999999
        url = self.get_product_category_detail_url(invalid_category_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(
            ProductCategory.objects.filter(id=self.category.id).exists()
        )
