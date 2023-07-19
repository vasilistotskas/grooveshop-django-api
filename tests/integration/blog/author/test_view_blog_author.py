import json

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from blog.models.author import BlogAuthor
from blog.serializers.author import BlogAuthorSerializer

User = get_user_model()


class BlogAuthorViewSetTestCase(APITestCase):
    author: BlogAuthor

    def setUp(self):
        self.user = User.objects.create_user(password="bar", email="email@email.com")
        self.author = BlogAuthor.objects.create(
            user_id=self.user.id, website="https://www.google.com"
        )

    def test_list(self):
        response = self.client.get("/api/v1/blog/author/")
        authors = BlogAuthor.objects.all()
        serializer = BlogAuthorSerializer(authors, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        new_user = User.objects.create_user(password="bar", email="email_new@email.com")
        payload = {
            "translations": {},
            "user": new_user.id,
            "website": "https://www.google.com",
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "bio": f"Translation for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.post(
            "/api/v1/blog/author/", json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {"user": "INVALID", "website": ""}
        response = self.client.post(
            "/api/v1/blog/author/", json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(f"/api/v1/blog/author/{self.author.id}/")
        author = BlogAuthor.objects.get(id=self.author.id)
        serializer = BlogAuthorSerializer(author)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_id = BlogAuthor.objects.latest("id").id + 1
        response = self.client.get(f"/api/v1/blog/author/{invalid_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "translations": {},
            "user": self.user.id,
            "website": "https://www.google.com",
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "bio": f"Translation for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.put(
            f"/api/v1/blog/author/{self.author.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {"user": "", "website": ""}
        response = self.client.put(
            f"/api/v1/blog/author/{self.author.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {"user": self.user.id}
        response = self.client.patch(
            f"/api/v1/blog/author/{self.author.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {"user": ""}
        response = self.client.patch(
            f"/api/v1/blog/author/{self.author.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(f"/api/v1/blog/author/{self.author.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        invalid_id = BlogAuthor.objects.latest("id").id + 1
        response = self.client.delete(f"/api/v1/blog/author/{invalid_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
