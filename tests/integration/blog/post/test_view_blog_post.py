from __future__ import annotations

import json

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.post import BlogPost
from blog.models.tag import BlogTag
from blog.serializers.post import BlogPostSerializer

User = get_user_model()


class BlogPostViewSetTestCase(APITestCase):
    post: BlogPost

    def setUp(self):
        user = User.objects.create_user(password="bar", email="email@email.com")
        category = BlogCategory.objects.create(
            name="name", slug="slug", description="description"
        )
        author = BlogAuthor.objects.create(
            user_id=user.id, website="https://www.google.com", bio="bio"
        )
        self.post = BlogPost.objects.create(
            slug="slug",
            title="title",
            subtitle="subtitle",
            category_id=category.id,
            author_id=author.id,
        )

    def test_list(self):
        response = self.client.get("/api/v1/blog/post/")
        posts = BlogPost.objects.all()
        serializer = BlogPostSerializer(posts, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        user = User.objects.create_user(password="bar_one", email="email_one@email.com")
        category = BlogCategory.objects.create(
            name="name_one", slug="slug_one", description="description_one"
        )
        author = BlogAuthor.objects.create(
            user_id=user.id, website="https://www.google.com", bio="bio_one"
        )
        tag = BlogTag.objects.create(name="name", active=True)
        payload = {
            "translations": {},
            "slug": "slug_one",
            "title": "title_one",
            "body": "body_one",
            "subtitle": "subtitle_one",
            "category": category.id,
            "author": author.id,
            "likes": [user.id],
            "tags": [tag.id],
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Translation for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.post(
            "/api/v1/blog/post/", json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "slug": "slug",
            "title": "title",
            "body": "",
            "subtitle": "subtitle",
            "category": "",
            "author": "",
        }
        response = self.client.post(
            "/api/v1/blog/post/", json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(f"/api/v1/blog/post/{self.post.id}/")
        post = BlogPost.objects.get(id=self.post.id)
        serializer = BlogPostSerializer(post)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_id = BlogPost.objects.latest("id").id + 1
        response = self.client.get(f"/api/v1/blog/post/{invalid_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        user = User.objects.create_user(password="bar_two", email="email_two@email.com")
        category = BlogCategory.objects.create(
            name="name_two", slug="slug_two", description="description_two"
        )
        author = BlogAuthor.objects.create(
            user_id=user.id, website="https://www.google.com", bio="bio_two"
        )
        tag = BlogTag.objects.create(name="name_two", active=True)
        payload = {
            "translations": {},
            "slug": "slug_two",
            "title": "title_two",
            "body": "body_two",
            "subtitle": "subtitle_two",
            "category": category.id,
            "author": author.id,
            "likes": [user.id],
            "tags": [tag.id],
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Translation for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.put(
            f"/api/v1/blog/post/{self.post.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "slug": "slug",
            "title": "title",
            "body": "",
            "subtitle": "subtitle",
            "category": "",
            "author": "",
        }
        response = self.client.put(
            f"/api/v1/blog/post/{self.post.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        user = User.objects.create_user(
            password="bar_three", email="email_three@email.com"
        )
        category = BlogCategory.objects.create(
            name="name_three", slug="slug_three", description="description_three"
        )
        author = BlogAuthor.objects.create(
            user_id=user.id, website="https://www.google.com", bio="bio_three"
        )
        payload = {
            "slug": "slug_three",
            "title": "title_three",
            "body": "body_three",
            "subtitle": "subtitle_three",
            "category": category.id,
            "author": author.id,
        }
        response = self.client.patch(
            f"/api/v1/blog/post/{self.post.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        invalid_category_id = BlogCategory.objects.latest("id").id + 1
        invalid_author_id = BlogAuthor.objects.latest("id").id + 1
        payload = {
            "slug": "slug",
            "title": "title",
            "body": "body",
            "subtitle": "subtitle",
            "category": invalid_category_id,
            "author": invalid_author_id,
        }
        response = self.client.patch(
            f"/api/v1/blog/post/{self.post.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(f"/api/v1/blog/post/{self.post.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        invalid_id = BlogPost.objects.latest("id").id + 1
        response = self.client.delete(f"/api/v1/blog/post/{invalid_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
