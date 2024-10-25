from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from blog.factories.author import BlogAuthorFactory
from blog.models.author import BlogAuthor
from blog.serializers.author import BlogAuthorSerializer
from user.factories.account import UserAccountFactory

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE

User = get_user_model()


class BlogAuthorViewSetTestCase(APITestCase):
    author: BlogAuthor = None

    def setUp(self):
        user = UserAccountFactory(num_addresses=0)
        self.author = BlogAuthorFactory(user=user)

    @staticmethod
    def get_author_detail_url(pk):
        return reverse("blog-author-detail", args=[pk])

    @staticmethod
    def get_author_list_url():
        return reverse("blog-author-list")

    def test_list(self):
        url = self.get_author_list_url()
        response = self.client.get(url)
        authors = BlogAuthor.objects.all()
        serializer = BlogAuthorSerializer(authors, many=True)

        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        user = UserAccountFactory(num_addresses=0)
        payload = {
            "user": user.id,
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "bio": f"New Author Bio in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_author_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "user": "invalid_user_id",
            "translations": {
                "invalid_lang_code": {
                    "bio": "Translation for invalid language code",
                },
            },
        }

        url = self.get_author_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_author_detail_url(self.author.pk)
        response = self.client.get(url)
        author = BlogAuthor.objects.get(pk=self.author.pk)
        serializer = BlogAuthorSerializer(author)

        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_author_id = 9999
        url = self.get_author_detail_url(invalid_author_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        user = UserAccountFactory(num_addresses=0)
        payload = {
            "user": user.id,
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "bio": f"Updated Author Bio in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_author_detail_url(self.author.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "user": "invalid_user_id",
            "translations": {
                "invalid_lang_code": {
                    "bio": "Translation for invalid language code",
                },
            },
        }

        url = self.get_author_detail_url(self.author.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "translations": {
                default_language: {
                    "bio": f"Updated bio in {default_language}",
                }
            },
        }

        url = self.get_author_detail_url(self.author.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "user": "invalid_user_id",
            "translations": {
                "invalid_lang_code": {
                    "bio": "Translation for invalid language code",
                },
            },
        }

        url = self.get_author_detail_url(self.author.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_author_detail_url(self.author.pk)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(BlogAuthor.objects.filter(pk=self.author.pk).exists())

    def test_destroy_invalid(self):
        invalid_author_id = 9999
        url = self.get_author_detail_url(invalid_author_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
