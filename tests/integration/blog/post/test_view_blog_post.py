from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from blog.factories.author import BlogAuthorFactory
from blog.factories.category import BlogCategoryFactory
from blog.factories.post import BlogPostFactory
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

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.author = BlogAuthorFactory(user=self.user)
        self.category = BlogCategoryFactory(slug="sample-category")

        self.post = BlogPostFactory(
            author=self.author,
            category=None,
            num_tags=0,
            num_comments=0,
        )
        self.post.likes.set([])
        self.post.tags.set([])

    @staticmethod
    def get_post_detail_url(pk):
        return reverse("blog-post-detail", args=[pk])

    @staticmethod
    def get_post_list_url():
        return reverse("blog-post-list")

    def test_list(self):
        url = self.get_post_list_url()
        response = self.client.get(url)
        serializer = BlogPostSerializer(BlogPost.objects.all(), many=True)

        self.assertEqual(response.data["results"], serializer.data)
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

    def tearDown(self) -> None:
        BlogPost.objects.all().delete()
        BlogCategory.objects.all().delete()
        BlogAuthor.objects.all().delete()
        User.objects.all().delete()
        super().tearDown()
