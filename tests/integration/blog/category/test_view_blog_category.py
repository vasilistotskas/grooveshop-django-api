from __future__ import annotations

import json
import os

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from app.settings import BASE_DIR
from blog.models.category import BlogCategory
from blog.serializers.category import BlogCategorySerializer


class BlogCategoryViewSetTestCase(APITestCase):
    image: str | SimpleUploadedFile = ""
    category: BlogCategory

    def setUp(self):
        self.category = BlogCategory.objects.create(slug="slug", image=self.image)

    def test_list(self):
        response = self.client.get("/api/v1/blog/category/")
        categories = BlogCategory.objects.all()
        serializer = BlogCategorySerializer(categories, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "slug": "slug_one",
            "translations": {},
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Translation for {language_name}",
                "description": f"Translation for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.post(
            "/api/v1/blog/category/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "slug": "",
        }
        response = self.client.post(
            "/api/v1/blog/category/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(f"/api/v1/blog/category/{self.category.id}/")
        category = BlogCategory.objects.get(id=self.category.id)
        serializer = BlogCategorySerializer(category)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_id = BlogCategory.objects.latest("id").id + 1
        response = self.client.get(f"/api/v1/blog/category/{invalid_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "translations": {},
            "slug": "slug_two",
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Translation for {language_name}",
                "description": f"Translation for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.put(
            f"/api/v1/blog/category/{self.category.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {"slug": ""}
        response = self.client.put(
            f"/api/v1/blog/category/{self.category.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "slug": "slug_three",
        }
        response = self.client.patch(
            f"/api/v1/blog/category/{self.category.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "slug": "",
        }
        response = self.client.patch(
            f"/api/v1/blog/category/{self.category.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(f"/api/v1/blog/category/{self.category.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        invalid_id = BlogCategory.objects.latest("id").id + 1
        response = self.client.delete(f"/api/v1/blog/category/{invalid_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class WithImage(BlogCategoryViewSetTestCase):
    image: str | SimpleUploadedFile = "uploads/blog/no_photo.jpg"

    def setUp(self):
        super().setUp()
        image_path = os.path.join(BASE_DIR, "files/images") + "/no_photo.jpg"
        with open(image_path, "rb") as image:
            self.image = SimpleUploadedFile(
                name="no_photo.jpg", content=image.read(), content_type="image/jpeg"
            )
        self.category.image = self.image
        self.category.save()


class WithoutImage(BlogCategoryViewSetTestCase):
    image: str | SimpleUploadedFile = ""
