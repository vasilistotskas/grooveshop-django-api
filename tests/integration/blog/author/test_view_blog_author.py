from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from blog.factories.author import BlogAuthorFactory
from blog.factories.post import BlogPostFactory
from blog.models.author import BlogAuthor
from blog.serializers.author import (
    BlogAuthorDetailSerializer,
)
from core.utils.testing import TestURLFixerMixin
from user.factories.account import UserAccountFactory

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE

User = get_user_model()


class BlogAuthorViewSetTestCase(TestURLFixerMixin, APITestCase):
    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0, is_superuser=False)
        self.admin_user = UserAccountFactory(num_addresses=0, is_superuser=True)
        self.author_user = UserAccountFactory(
            num_addresses=0, is_superuser=False
        )
        self.blog_author = BlogAuthorFactory(user=self.author_user)

        self.blog_post_1 = BlogPostFactory(author=self.blog_author)
        self.blog_post_2 = BlogPostFactory(author=self.blog_author)

        self.client.force_authenticate(user=self.user)

    def get_blog_author_detail_url(self, pk):
        return reverse("blog-author-detail", args=[pk])

    def get_blog_author_list_url(self):
        return reverse("blog-author-list")

    def get_blog_author_posts_url(self, pk):
        return reverse("blog-author-posts", args=[pk])

    def test_list_uses_correct_serializer(self):
        url = self.get_blog_author_list_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        if response.data["results"]:
            author_data = response.data["results"][0]
            required_fields = ["id", "user", "website", "uuid", "translations"]
            for field in required_fields:
                self.assertIn(
                    field,
                    author_data,
                    f"Field '{field}' missing from list response",
                )

    def test_retrieve_uses_correct_serializer(self):
        url = self.get_blog_author_detail_url(self.blog_author.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_basic_fields = {
            "id",
            "user",
            "website",
            "created_at",
            "updated_at",
            "uuid",
            "translations",
            "number_of_posts",
            "total_likes_received",
            "recent_posts",
            "top_posts",
        }
        self.assertTrue(
            expected_basic_fields.issubset(set(response.data.keys()))
        )

    def test_create_request_response_serializers(self):
        new_user = UserAccountFactory(num_addresses=0, is_superuser=False)
        payload = {
            "user": new_user.id,
            "website": "https://example.com",
            "translations": {
                default_language: {
                    "bio": "Test author bio",
                }
            },
        }

        url = self.get_blog_author_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        expected_basic_fields = {
            "id",
            "user",
            "website",
            "created_at",
            "updated_at",
            "uuid",
            "translations",
            "number_of_posts",
            "total_likes_received",
        }
        actual_fields = set(response.data.keys())
        self.assertTrue(expected_basic_fields.issubset(actual_fields))

        author = BlogAuthor.objects.get(id=response.data["id"])
        self.assertEqual(author.website, "https://example.com")
        self.assertEqual(author.user.id, new_user.id)

    def test_update_request_response_serializers(self):
        new_user = UserAccountFactory(num_addresses=0, is_superuser=False)
        payload = {
            "user": new_user.id,
            "website": "https://updated.com",
            "translations": {
                default_language: {
                    "bio": "Updated author bio",
                }
            },
        }

        url = self.get_blog_author_detail_url(self.blog_author.id)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_basic_fields = {
            "id",
            "user",
            "website",
            "created_at",
            "updated_at",
            "uuid",
            "translations",
            "number_of_posts",
            "total_likes_received",
        }
        self.assertTrue(
            expected_basic_fields.issubset(set(response.data.keys()))
        )

        author = BlogAuthor.objects.get(id=response.data["id"])
        self.assertEqual(author.website, "https://updated.com")

    def test_partial_update_request_response_serializers(self):
        payload = {
            "website": "https://partially-updated.com",
            "translations": {
                default_language: {
                    "bio": "Partially updated bio",
                }
            },
        }

        url = self.get_blog_author_detail_url(self.blog_author.id)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_basic_fields = {
            "id",
            "user",
            "website",
            "created_at",
            "updated_at",
            "uuid",
            "translations",
            "number_of_posts",
            "total_likes_received",
        }
        self.assertTrue(
            expected_basic_fields.issubset(set(response.data.keys()))
        )

        author = BlogAuthor.objects.get(id=response.data["id"])
        self.assertEqual(author.website, "https://partially-updated.com")

    def test_delete_endpoint(self):
        url = self.get_blog_author_detail_url(self.blog_author.id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            BlogAuthor.objects.filter(id=self.blog_author.id).exists()
        )

    def test_filtering_functionality(self):
        url = self.get_blog_author_list_url()

        response = self.client.get(url, {"user_id": self.author_user.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(url, {"website": self.blog_author.website})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_ordering_functionality(self):
        url = self.get_blog_author_list_url()

        response = self.client.get(url, {"ordering": "created_at"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(url, {"ordering": "user__first_name"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_search_functionality(self):
        url = self.get_blog_author_list_url()

        response = self.client.get(url, {"search": self.author_user.first_name})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_validation_errors_consistent(self):
        payload = {
            "user": 99999,
            "website": "invalid-url",
        }

        url = self.get_blog_author_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_duplicate_user_validation(self):
        payload = {
            "user": self.author_user.id,
            "website": "https://example.com",
            "translations": {
                default_language: {
                    "bio": "Duplicate author attempt",
                }
            },
        }

        url = self.get_blog_author_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "This user already has an author profile", str(response.data)
        )

    def test_posts_endpoint(self):
        url = self.get_blog_author_posts_url(self.blog_author.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        self.assertEqual(len(response.data["results"]), 2)

        for post_data in response.data["results"]:
            if isinstance(post_data["author"], dict):
                self.assertEqual(post_data["author"]["id"], self.blog_author.id)
            else:
                self.assertEqual(post_data["author"], self.blog_author.id)

    def test_website_validation(self):
        new_user = UserAccountFactory(num_addresses=0, is_superuser=False)

        payload = {
            "user": new_user.id,
            "website": "not-a-valid-url",
            "translations": {
                default_language: {
                    "bio": "Test bio",
                }
            },
        }

        url = self.get_blog_author_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("website", response.data)
        self.assertTrue(
            any(
                "invalid" in str(error).lower() or "url" in str(error).lower()
                for error in response.data["website"]
            )
        )

    def test_detail_serializer_computed_fields(self):
        url = self.get_blog_author_detail_url(self.blog_author.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("number_of_posts", response.data)
        self.assertIn("total_likes_received", response.data)
        self.assertIn("recent_posts", response.data)
        self.assertIn("top_posts", response.data)

        self.assertEqual(response.data["number_of_posts"], 2)
        self.assertIsInstance(response.data["recent_posts"], list)
        self.assertIsInstance(response.data["top_posts"], list)

    def test_user_serialization_in_detail(self):
        url = self.get_blog_author_detail_url(self.blog_author.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        user_data = response.data["user"]
        self.assertIsInstance(user_data, dict)
        self.assertIn("pk", user_data)
        self.assertIn("email", user_data)
        self.assertEqual(user_data["pk"], self.author_user.id)

    def test_consistency_with_manual_serializer_instantiation(self):
        url = self.get_blog_author_detail_url(self.blog_author.id)
        viewset_response = self.client.get(url)

        serializer = BlogAuthorDetailSerializer(
            self.blog_author,
            context={"request": self.client.request().wsgi_request},
        )

        self.assertEqual(viewset_response.status_code, status.HTTP_200_OK)

        viewset_data = viewset_response.data
        serializer_data = serializer.data

        key_fields = ["id", "website", "uuid"]
        for field in key_fields:
            self.assertEqual(
                viewset_data[field],
                serializer_data[field],
                f"Field '{field}' differs between ViewSet and manual serializer",
            )
