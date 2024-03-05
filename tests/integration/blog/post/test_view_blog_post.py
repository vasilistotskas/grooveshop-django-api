from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.post import BlogPost
from blog.serializers.post import BlogPostSerializer
from helpers.seed import get_or_create_default_image

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.memory.InMemoryStorage",
        },
    }
)
class BlogPostViewSetTestCase(APITestCase):
    post: BlogPost = None
    user: User = None
    author: BlogAuthor = None
    category: BlogCategory = None

    def setUp(self):
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        self.author = BlogAuthor.objects.create(user=self.user)

        image_category = get_or_create_default_image("uploads/blog/no_photo.jpg")
        self.category = BlogCategory.objects.create(
            slug="sample-category", image=image_category
        )
        for language in languages:
            self.category.set_current_language(language)
            self.category.name = f"Category name in {language}"
            self.category.description = f"Category description in {language}"
            self.category.save()
        self.category.set_current_language(default_language)

        image_post = get_or_create_default_image("uploads/blog/no_photo.jpg")
        self.post = BlogPost.objects.create(
            title="Test Post",
            author=self.author,
            status="draft",
            image=image_post,
            category=None,
            featured=False,
            view_count=0,
        )
        self.post.likes.set([])
        self.post.tags.set([])

        for language in languages:
            self.post.set_current_language(language)
            self.post.title = f"Test Post Title in {language}"
            self.post.subtitle = f"Test Post Subtitle in {language}"
            self.post.body = f"This is a test post body in {language}"
            self.post.save()
        self.post.set_current_language(default_language)

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
        super().tearDown()
        self.post.delete()
        self.author.delete()
        self.user.delete()
        self.category.delete()
