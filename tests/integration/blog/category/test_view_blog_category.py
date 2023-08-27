from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from blog.models.category import BlogCategory
from blog.serializers.category import BlogCategorySerializer

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class BlogCategoryViewSetTestCase(APITestCase):
    category: BlogCategory = None

    def setUp(self):
        self.category = BlogCategory.objects.create(slug="test-category")

        for language in languages:
            self.category.set_current_language(language)
            self.category.name = f"Category Name in {language}"
            self.category.description = f"Category Description in {language}"
            self.category.save()
        self.category.set_current_language(default_language)

    @staticmethod
    def get_category_detail_url(pk):
        return reverse("blog-category-detail", args=[pk])

    @staticmethod
    def get_category_list_url():
        return reverse("blog-category-list")

    def test_list(self):
        url = self.get_category_list_url()
        response = self.client.get(url)
        categories = BlogCategory.objects.all()
        serializer = BlogCategorySerializer(categories, many=True)

        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "slug": "new-category",
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"New Category Name in {language_name}",
                "description": f"New Category Description in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_category_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "slug": False,
            "image": "invalid_image",
            "translations": {
                "invalid_lang_code": {
                    "description": "Translation for invalid language code",
                },
            },
        }

        url = self.get_category_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_category_detail_url(self.category.pk)
        response = self.client.get(url)
        category = BlogCategory.objects.get(pk=self.category.pk)
        serializer = BlogCategorySerializer(category)

        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_category_id = 9999  # An ID that doesn't exist in the database
        url = self.get_category_detail_url(invalid_category_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "slug": "updated-category",
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Updated Category Name in {language_name}",
                "description": f"Updated Category Description in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_category_detail_url(self.category.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "slug": False,
            "image": "invalid_image",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                    "description": "Translation for invalid language code",
                },
            },
        }

        url = self.get_category_detail_url(self.category.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "translations": {
                default_language: {
                    "name": f"Partial update for {default_language}",
                },
            },
        }

        url = self.get_category_detail_url(self.category.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "slug": "",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                    "description": "Translation for invalid language code",
                },
            },
        }

        url = self.get_category_detail_url(self.category.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_category_detail_url(self.category.pk)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(BlogCategory.objects.filter(pk=self.category.pk).exists())

    def test_destroy_invalid(self):
        invalid_category_id = 9999  # An ID that doesn't exist in the database
        url = self.get_category_detail_url(invalid_category_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def tearDown(self) -> None:
        super().tearDown()
        self.category.delete()
