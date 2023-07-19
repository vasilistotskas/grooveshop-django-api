from __future__ import annotations

import json

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.comment import BlogComment
from blog.models.post import BlogPost
from blog.serializers.comment import BlogCommentSerializer

User = get_user_model()


class BlogCommentViewSetTestCase(APITestCase):
    comment: BlogComment

    def setUp(self):
        user = User.objects.create_user(password="bar", email="email@email.com")
        category = BlogCategory.objects.create(
            name="name", slug="slug", description="description"
        )
        author = BlogAuthor.objects.create(
            user_id=user.id, website="https://www.google.com", bio="bio"
        )
        post = BlogPost.objects.create(
            slug="slug",
            title="title",
            subtitle="subtitle",
            category_id=category.id,
            author_id=author.id,
        )
        self.comment = BlogComment.objects.create(post_id=post.id, user_id=user.id)

    def test_list(self):
        response = self.client.get("/api/v1/blog/comment/")
        comments = BlogComment.objects.all()
        serializer = BlogCommentSerializer(comments, many=True)
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
        post = BlogPost.objects.create(
            slug="slug_one",
            title="title_one",
            subtitle="subtitle_one",
            category_id=category.id,
            author_id=author.id,
        )
        post.likes.add(user)

        payload = {
            "translations": {},
            "post": post.id,
            "user": user.id,
            "likes": [user.id],
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "content": f"Translation for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.post(
            "/api/v1/blog/comment/",
            json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "post": "",
            "content": "",
        }
        response = self.client.post(
            "/api/v1/blog/comment/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(f"/api/v1/blog/comment/{self.comment.id}/")
        comment = BlogComment.objects.get(id=self.comment.id)
        serializer = BlogCommentSerializer(comment)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_id = BlogComment.objects.latest("id").id + 1
        response = self.client.get(f"/api/v1/blog/comment/{invalid_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        user = User.objects.create_user(password="bar_two", email="email_two@email.com")
        category = BlogCategory.objects.create(
            name="name_two", slug="slug_two", description="description_two"
        )
        author = BlogAuthor.objects.create(
            user_id=user.id, website="https://www.google.com", bio="bio_two"
        )
        post = BlogPost.objects.create(
            slug="slug_two",
            title="title_two",
            subtitle="subtitle_two",
            category_id=category.id,
            author_id=author.id,
        )

        payload = {
            "translations": {},
            "post": post.id,
            "user": user.id,
            "likes": [user.id],
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "content": f"Translation for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.put(
            f"/api/v1/blog/comment/{self.comment.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "post": "",
        }
        response = self.client.put(
            f"/api/v1/blog/comment/{self.comment.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {}
        response = self.client.patch(
            f"/api/v1/blog/comment/{self.comment.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "post": "",
        }
        response = self.client.patch(
            f"/api/v1/blog/comment/{self.comment.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(f"/api/v1/blog/comment/{self.comment.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        invalid_id = BlogComment.objects.latest("id").id + 1
        response = self.client.delete(f"/api/v1/blog/comment/{invalid_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
