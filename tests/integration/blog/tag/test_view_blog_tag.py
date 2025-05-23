from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from blog.factories.tag import BlogTagFactory
from blog.models.tag import BlogTag
from core.utils.testing import TestURLFixerMixin

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class BlogTagViewSetTestCase(TestURLFixerMixin, APITestCase):
    tag: BlogTag = None

    def setUp(self):
        self.tag = BlogTagFactory(active=True)

    @staticmethod
    def get_tag_detail_url(pk):
        return reverse("blog-tag-detail", args=[pk])

    @staticmethod
    def get_tag_list_url():
        return reverse("blog-tag-list")

    def test_list(self):
        url = self.get_tag_list_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertIsInstance(response.data["results"], list)

        tags_count = BlogTag.objects.count()
        self.assertEqual(len(response.data["results"]), tags_count)

        if tags_count > 0:
            first_result = response.data["results"][0]
            self.assertIn("translations", first_result)
            self.assertIn("active", first_result)

    def test_create_valid(self):
        payload = {
            "active": True,
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"New Tag name in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_tag_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "active": "invalid_active",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                },
            },
        }

        url = self.get_tag_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_tag_detail_url(self.tag.pk)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("active", response.data)
        self.assertEqual(response.data["active"], self.tag.active)

        self.assertIn("translations", response.data)

    def test_retrieve_invalid(self):
        invalid_tag_id = 9999
        url = self.get_tag_detail_url(invalid_tag_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "active": False,
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Updated Tag name in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_tag_detail_url(self.tag.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "active": "invalid_active",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                },
            },
        }

        url = self.get_tag_detail_url(self.tag.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "translations": {
                default_language: {
                    "name": f"Updated Tag name in {default_language}",
                }
            },
        }

        url = self.get_tag_detail_url(self.tag.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "active": "invalid",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                },
            },
        }

        url = self.get_tag_detail_url(self.tag.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_tag_detail_url(self.tag.pk)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(BlogTag.objects.filter(pk=self.tag.pk).exists())

    def test_destroy_invalid(self):
        invalid_tag_id = 9999
        url = self.get_tag_detail_url(invalid_tag_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
