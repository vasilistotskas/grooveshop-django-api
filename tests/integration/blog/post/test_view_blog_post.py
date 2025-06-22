from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from blog.factories.author import BlogAuthorFactory
from blog.factories.category import BlogCategoryFactory
from blog.factories.post import BlogPostFactory
from blog.factories.tag import BlogTagFactory
from blog.models.post import BlogPost
from core.utils.testing import TestURLFixerMixin
from user.factories.account import UserAccountFactory

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class BlogPostViewSetTestCase(TestURLFixerMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = UserAccountFactory()
        cls.author = BlogAuthorFactory(user=cls.user)
        cls.category = BlogCategoryFactory()
        cls.tag1 = BlogTagFactory()
        cls.tag2 = BlogTagFactory()
        cls.post = BlogPostFactory(
            author=cls.author,
            category=cls.category,
            featured=True,
        )
        cls.post.tags.set([cls.tag1, cls.tag2])

    def get_post_list_url(self):
        return reverse("blog-post-list")

    def get_post_detail_url(self, pk):
        return reverse("blog-post-detail", args=[pk])

    def test_list(self):
        url = self.get_post_list_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertIn("count", response.data)
        if "links" in response.data:
            self.assertIn("next", response.data["links"])
            self.assertIn("previous", response.data["links"])
        else:
            self.assertIn("next", response.data)
            self.assertIn("previous", response.data)

        self.assertGreaterEqual(len(response.data["results"]), 1)

        first_result = response.data["results"][0]
        expected_basic_fields = {
            "id",
            "uuid",
            "slug",
            "likes",
            "translations",
            "author",
            "category",
            "tags",
            "featured",
            "view_count",
            "likes_count",
            "comments_count",
            "tags_count",
            "is_published",
            "published_at",
            "created_at",
            "updated_at",
            "main_image_path",
            "reading_time",
            "content_preview",
        }
        self.assertTrue(
            expected_basic_fields.issubset(set(first_result.keys()))
        )

    def test_list_uses_correct_serializer(self):
        url = self.get_post_list_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        first_result = response.data["results"][0]
        expected_fields = {
            "id",
            "uuid",
            "slug",
            "likes",
            "translations",
            "author",
            "category",
            "tags",
            "featured",
            "view_count",
            "likes_count",
            "comments_count",
            "tags_count",
            "is_published",
            "published_at",
            "created_at",
            "updated_at",
            "main_image_path",
            "reading_time",
            "content_preview",
        }
        self.assertTrue(expected_fields.issubset(set(first_result.keys())))

    def test_retrieve_valid(self):
        url = self.get_post_detail_url(self.post.id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "uuid",
            "slug",
            "likes",
            "translations",
            "author",
            "category",
            "tags",
            "featured",
            "view_count",
            "likes_count",
            "comments_count",
            "tags_count",
            "is_published",
            "published_at",
            "created_at",
            "updated_at",
            "main_image_path",
            "reading_time",
            "content_preview",
            "user_has_liked",
            "seo_title",
            "seo_description",
            "seo_keywords",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_retrieve_uses_correct_serializer(self):
        url = self.get_post_detail_url(self.post.id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        detail_only_fields = {
            "user_has_liked",
            "seo_title",
            "seo_description",
            "seo_keywords",
        }
        response_fields = set(response.data.keys())

        for field in detail_only_fields:
            self.assertIn(
                field,
                response_fields,
                f"Detail field '{field}' should be present in detail response",
            )

    def test_retrieve_invalid(self):
        invalid_post_id = 9999
        url = self.get_post_detail_url(invalid_post_id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_valid(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "slug": "new-test-post",
            "category": self.category.id,
            "tags": [self.tag1.id],
            "author": self.author.id,
            "featured": False,
            "translations": {},
        }

        for language in languages:
            language_code = language
            translation_payload = {
                "title": f"New Test Post Title in {language_code}",
                "subtitle": f"New Test Post Subtitle in {language_code}",
                "body": f"This is a new test post body in {language_code}",
            }
            payload["translations"][language_code] = translation_payload

        url = self.get_post_list_url()
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        expected_fields = {
            "id",
            "uuid",
            "slug",
            "likes",
            "translations",
            "author",
            "category",
            "tags",
            "featured",
            "view_count",
            "likes_count",
            "comments_count",
            "tags_count",
            "is_published",
            "published_at",
            "created_at",
            "updated_at",
            "main_image_path",
            "reading_time",
            "content_preview",
            "user_has_liked",
            "seo_title",
            "seo_description",
            "seo_keywords",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_create_invalid(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "author": "invalid_author_id",
            "translations": {
                "invalid_lang_code": {
                    "title": "Translation for invalid language code",
                },
            },
        }

        url = self.get_post_list_url()
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_valid(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "slug": "updated-test-post",
            "category": self.category.id,
            "tags": [self.tag1.id],
            "author": self.author.id,
            "translations": {},
        }

        for language in languages:
            language_code = language
            translation_payload = {
                "title": f"Updated Test Post Title in {language_code}",
                "subtitle": f"Updated Test Post Subtitle in {language_code}",
                "body": f"This is an updated test post body in {language_code}",
            }
            payload["translations"][language_code] = translation_payload

        url = self.get_post_detail_url(self.post.id)
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "uuid",
            "slug",
            "likes",
            "translations",
            "author",
            "category",
            "tags",
            "featured",
            "view_count",
            "likes_count",
            "comments_count",
            "tags_count",
            "is_published",
            "published_at",
            "created_at",
            "updated_at",
            "main_image_path",
            "reading_time",
            "content_preview",
            "user_has_liked",
            "seo_title",
            "seo_description",
            "seo_keywords",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_update_invalid(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "author": "invalid_author_id",
            "translations": {
                "invalid_lang_code": {
                    "content": "Translation for invalid language code",
                },
            },
        }

        url = self.get_post_detail_url(self.post.id)
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "translations": {
                default_language: {"title": "Partially Updated Post Title"}
            }
        }

        url = self.get_post_detail_url(self.post.id)
        response = self.client.patch(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "uuid",
            "slug",
            "likes",
            "translations",
            "author",
            "category",
            "tags",
            "featured",
            "view_count",
            "likes_count",
            "comments_count",
            "tags_count",
            "is_published",
            "published_at",
            "created_at",
            "updated_at",
            "main_image_path",
            "reading_time",
            "content_preview",
            "user_has_liked",
            "seo_title",
            "seo_description",
            "seo_keywords",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_partial_update_invalid(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "slug": "",
            "translations": {default_language: {"title": "Test Post"}},
        }

        url = self.get_post_detail_url(self.post.id)
        response = self.client.patch(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        self.client.force_authenticate(user=self.user)
        post_to_delete = BlogPostFactory(author=self.author)

        url = self.get_post_detail_url(post_to_delete.id)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        self.assertFalse(BlogPost.objects.filter(id=post_to_delete.id).exists())

    def test_destroy_invalid(self):
        self.client.force_authenticate(user=self.user)
        invalid_post_id = 9999

        url = self.get_post_detail_url(invalid_post_id)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_related_posts_response_structure(self):
        url = reverse("blog-post-related_posts", args=[self.post.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIsInstance(response.data, list)

    def test_related_posts_when_no_related_posts_exist(self):
        BlogPost.objects.exclude(id=self.post.id).delete()

        url = reverse("blog-post-related_posts", args=[self.post.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_related_posts_default_strategy_fills_limit(self):
        additional_default_posts = BlogPostFactory.create_batch(
            3,
            author=self.author,
            category=self.category,
            num_tags=0,
            num_comments=0,
        )
        for post in additional_default_posts:
            post.tags.set([])
            post.likes.set([])

        url = reverse("blog-post-related_posts", args=[self.post.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertLessEqual(len(response.data), 8)

    def test_comments_action(self):
        url = reverse("blog-post-comments", args=[self.post.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertIn("count", response.data)

    def test_trending_action(self):
        url = reverse("blog-post-trending")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertIn("count", response.data)

    def test_trending_action_with_days_parameter(self):
        url = reverse("blog-post-trending")
        response = self.client.get(url, {"days": 30})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertIn("count", response.data)

    def test_popular_action(self):
        url = reverse("blog-post-popular")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertIn("count", response.data)

    def test_featured_action(self):
        url = reverse("blog-post-featured")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertIn("count", response.data)

        for post_data in response.data["results"]:
            self.assertTrue(post_data["featured"])

    def test_update_likes_action_authenticated(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("blog-post-update_likes", args=[self.post.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_likes_action_unauthenticated(self):
        url = reverse("blog-post-update_likes", args=[self.post.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_update_view_count_action(self):
        original_view_count = self.post.view_count
        url = reverse("blog-post-update_view_count", args=[self.post.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.post.refresh_from_db()
        self.assertEqual(self.post.view_count, original_view_count + 1)

    def test_liked_posts_action_authenticated(self):
        self.client.force_authenticate(user=self.user)
        payload = {"post_ids": [self.post.id]}

        url = reverse("blog-post-liked_posts")
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("post_ids", response.data)
        self.assertIsInstance(response.data["post_ids"], list)

    def test_liked_posts_action_invalid_data(self):
        self.client.force_authenticate(user=self.user)
        payload = {"invalid_field": "test"}

        url = reverse("blog-post-liked_posts")
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_filtering_functionality(self):
        url = self.get_post_list_url()
        response = self.client.get(url, {"featured": "true"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        for post_data in response.data["results"]:
            self.assertTrue(post_data["featured"])

    def test_ordering_functionality(self):
        url = self.get_post_list_url()
        response = self.client.get(url, {"ordering": "view_count"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)

    def test_search_functionality(self):
        url = self.get_post_list_url()
        response = self.client.get(url, {"search": "test"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
