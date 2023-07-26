from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from blog.models.author import BlogAuthor
from blog.serializers.author import BlogAuthorSerializer
from user.models import UserAccount

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class BlogAuthorViewSetTestCase(APITestCase):
    author = None

    def setUp(self):
        user = UserAccount.objects.create(
            email="testuser@example.com", password="testpassword"
        )
        self.author = BlogAuthor.objects.create(user=user)

        for language in languages:
            self.author.set_current_language(language)
            self.author.bio = f"Bio in {language}"
            self.author.save()
        self.author.set_current_language(default_language)

    @staticmethod
    def get_author_detail_url(pk):
        return reverse("blog-author-detail", args=[pk])

    @staticmethod
    def get_author_list_url():
        return reverse("blog-author-list")

    def test_list_authors(self):
        url = self.get_author_list_url()
        response = self.client.get(url)
        authors = BlogAuthor.objects.all()
        serializer = BlogAuthorSerializer(authors, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        user = UserAccount.objects.create(
            email="testusercreate@example.com", password="testpassword"
        )
        payload = {
            "user": user.id,
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "bio": f"Bio for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_author_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "user": "invalid_user_id",  # An invalid user ID, should raise validation error
            "translations": {
                "invalid_lang_code": {
                    "bio": "Bio with invalid language code",
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
        invalid_author_id = 9999  # An ID that doesn't exist in the database
        url = self.get_author_detail_url(invalid_author_id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        user = UserAccount.objects.create(
            email="testuserupdate@example.com", password="testpassword"
        )
        payload = {
            "user": user.id,
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "bio": f"Updated bio for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_author_detail_url(self.author.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "user": "invalid_user_id",  # An invalid user ID, should raise validation error
            "translations": {
                "invalid_lang_code": {
                    "bio": "Bio with invalid language code",
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
            "user": "invalid_user_id",  # An invalid user ID, should raise validation error
            "translations": {
                "invalid_lang_code": {
                    "bio": "Partial update with invalid language code",
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
        invalid_author_id = 9999  # An ID that doesn't exist in the database
        url = self.get_author_detail_url(invalid_author_id)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
