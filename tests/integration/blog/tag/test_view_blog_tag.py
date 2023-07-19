from __future__ import annotations

import json

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from blog.models.tag import BlogTag
from blog.serializers.tag import BlogTagSerializer

User = get_user_model()


class BlogTagViewSetTestCase(APITestCase):
    tag: BlogTag

    def setUp(self):
        self.tag = BlogTag.objects.create()

    def test_list(self):
        response = self.client.get("/api/v1/blog/tag/")
        tags = BlogTag.objects.all()
        serializer = BlogTagSerializer(tags, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {"translations": {}}

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Translation for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.post(
            "/api/v1/blog/tag/", json.dumps(payload), content_type="application/json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {"translations": {}}

        response = self.client.post(
            "/api/v1/blog/tag/", json.dumps(payload), content_type="application/json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(f"/api/v1/blog/tag/{self.tag.id}/")
        tag = BlogTag.objects.get(id=self.tag.id)
        serializer = BlogTagSerializer(tag)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_id = BlogTag.objects.latest("id").id + 1
        response = self.client.get(f"/api/v1/blog/tag/{invalid_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "translations": {},
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Translation for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.put(
            f"/api/v1/blog/tag/{self.tag.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {}
        response = self.client.put(
            f"/api/v1/blog/tag/{self.tag.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "name": "test_three",
        }
        response = self.client.patch(
            f"/api/v1/blog/tag/{self.tag.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "active": "invalid",
        }
        response = self.client.patch(
            f"/api/v1/blog/tag/{self.tag.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(f"/api/v1/blog/tag/{self.tag.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        invalid_id = BlogTag.objects.latest("id").id + 1
        response = self.client.delete(f"/api/v1/blog/tag/{invalid_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
