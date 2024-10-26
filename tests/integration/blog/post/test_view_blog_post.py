from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import translation
from rest_framework import status
from rest_framework.test import APITestCase

from blog.factories.author import BlogAuthorFactory
from blog.factories.category import BlogCategoryFactory
from blog.factories.post import BlogPostFactory
from blog.factories.tag import BlogTagFactory
from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.post import BlogPost
from blog.serializers.post import BlogPostSerializer
from user.factories.account import UserAccountFactory

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class BlogPostViewSetTestCase(APITestCase):
    post: BlogPost = None
    user: User = None
    author: BlogAuthor = None
    category: BlogCategory = None
    other_category: BlogCategory = None
    tag1 = None
    tag2 = None
    tag3 = None
    default_related_posts = []
    tag_based_related_posts = []

    def setUp(self):
        translation.activate(default_language)
        self.addCleanup(translation.deactivate)
        self.user = UserAccountFactory(num_addresses=0)
        self.author = BlogAuthorFactory(user=self.user)
        self.category = BlogCategoryFactory(slug="sample-category")
        self.other_category = BlogCategoryFactory(slug="other-category")

        self.tag1 = BlogTagFactory(name="Django")
        self.tag2 = BlogTagFactory(name="Python")
        self.tag3 = BlogTagFactory(name="REST")

        self.post = BlogPostFactory(
            author=self.author,
            category=self.category,
            num_tags=2,
            num_comments=0,
        )
        self.post.tags.set([self.tag1, self.tag2])
        self.post.likes.set([])

        self.default_related_posts = BlogPostFactory.create_batch(
            5,
            author=self.author,
            category=self.category,
            num_tags=0,
            num_comments=0,
        )

        self.tag_based_related_posts = BlogPostFactory.create_batch(
            3,
            author=self.author,
            category=self.other_category,
            num_tags=1,
            num_comments=0,
        )
        for i, post in enumerate(self.tag_based_related_posts):
            if i % 2 == 0:
                post.tags.set([self.tag1])
            else:
                post.tags.set([self.tag2])
            post.likes.set([])

    @staticmethod
    def get_post_detail_url(pk):
        return reverse("blog-post-detail", args=[pk])

    @staticmethod
    def get_post_list_url():
        return reverse("blog-post-list")

    def test_list(self):
        url = self.get_post_list_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "slug": "new-test-post",
            "category": self.category.id,
            "likes": [],
            "tags": [],
            "author": self.author.id,
            "status": "DRAFT",
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "title": f"New Test Post Title in {language_name}",
                "subtitle": f"New Test Post Subtitle in {language_name}",
                "body": f"This is a new test post body in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_post_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "author": "invalid_author_id",
            "translations": {
                "invalid_lang_code": {
                    "title": "Translation for invalid language code",
                    "subtitle": "Translation for invalid language code",
                    "body": "Translation for invalid language code",
                }
            },
        }

        url = self.get_post_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_post_detail_url(self.post.id)
        response = self.client.get(url)
        post = BlogPost.objects.get(id=self.post.id)
        serializer = BlogPostSerializer(post)

        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_post_id = 9999
        url = self.get_post_detail_url(invalid_post_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "slug": "updated-test-post",
            "category": self.category.id,
            "likes": [],
            "tags": [],
            "status": "PUBLISHED",
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "title": f"Updated Test Post Title in {language_name}",
                "subtitle": f"Updated Test Post Subtitle in {language_name}",
                "body": f"This is an updated test post body in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_post_detail_url(self.post.id)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "author": "invalid_author_id",
            "status": "invalid_status",
            "translations": {
                "invalid_lang_code": {
                    "content": "Translation for invalid language code",
                },
            },
        }

        url = self.get_post_detail_url(self.post.id)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "translations": {
                default_language: {
                    "title": "Partial Update Test Post Title",
                },
            },
        }

        url = self.get_post_detail_url(self.post.id)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "author": "invalid_author_id",
            "translations": {
                "invalid_lang_code": {
                    "title": "Translation for invalid language code",
                },
            },
        }

        url = self.get_post_detail_url(self.post.id)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_post_detail_url(self.post.id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(BlogPost.objects.filter(id=self.post.id).exists())

    def test_destroy_invalid(self):
        invalid_post_id = 9999
        url = self.get_post_detail_url(invalid_post_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

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
        self.assertEqual(len(response.data), 8)

        allowed_categories = [self.category.id, self.other_category.id]

        for related_post in response.data:
            self.assertIn(related_post["category"], allowed_categories)
            self.assertNotEqual(related_post["id"], self.post.id)

    def test_related_posts_when_no_related_posts_exist(self):
        BlogPost.objects.exclude(id=self.post.id).delete()

        url = reverse("blog-post-related_posts", args=[self.post.id])
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_related_posts_response_structure(self):
        url = reverse("blog-post-related_posts", args=[self.post.id])
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        if response.data:
            first_related_post = response.data[0]
            serializer = BlogPostSerializer(BlogPost.objects.get(id=first_related_post["id"]))
            expected_data = serializer.data
            self.assertEqual(first_related_post, expected_data)
