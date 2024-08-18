from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from blog.factories.author import BlogAuthorFactory
from blog.factories.comment import BlogCommentFactory
from blog.factories.post import BlogPostFactory
from blog.models.author import BlogAuthor
from blog.models.comment import BlogComment
from blog.models.post import BlogPost
from blog.serializers.comment import BlogCommentSerializer
from user.factories.account import UserAccountFactory

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class BlogCommentViewSetTestCase(APITestCase):
    comment: BlogComment = None
    user: User = None
    post: BlogPost = None
    author: BlogAuthor = None

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.client.force_authenticate(user=self.user)
        self.author = BlogAuthorFactory(user=self.user)
        self.post = BlogPostFactory(
            author=self.author,
            num_tags=0,
            num_comments=0,
        )
        self.comment = BlogCommentFactory(is_approved=True, user=self.user, post=self.post)

    @staticmethod
    def get_comment_detail_url(pk):
        return reverse("blog-comment-detail", args=[pk])

    @staticmethod
    def get_comment_list_url():
        return reverse("blog-comment-list")

    def test_list(self):
        url = self.get_comment_list_url()
        response = self.client.get(url)
        comments = BlogComment.objects.all()
        serializer = BlogCommentSerializer(comments, many=True)

        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        user = UserAccountFactory(num_addresses=0)
        author = BlogAuthorFactory(user=user)
        post = BlogPostFactory(
            author=author,
            num_tags=0,
            num_comments=0,
        )

        payload = {
            "user": user.id,
            "post": post.id,
            "is_approved": True,
            "likes": [],
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "content": f"New Comment Content in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_comment_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "user": "invalid_user_id",
            "post": "invalid_post_id",
            "translations": {
                "invalid_lang_code": {
                    "content": "Translation for invalid language code",
                }
            },
        }

        url = self.get_comment_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_comment_detail_url(self.comment.pk)
        response = self.client.get(url)
        comment = BlogComment.objects.get(pk=self.comment.pk)
        serializer = BlogCommentSerializer(comment)

        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_comment_id = 9999
        url = self.get_comment_detail_url(invalid_comment_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "user": self.user.id,
            "post": self.post.id,
            "is_approved": False,
            "likes": [],
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "content": f"Updated Comment Content in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_comment_detail_url(self.comment.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "user": "invalid_user_id",
            "post": "invalid_post_id",
            "translations": {
                "invalid_lang_code": {
                    "content": "Translation for invalid language code",
                },
            },
        }

        url = self.get_comment_detail_url(self.comment.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "translations": {
                default_language: {"content": f"Partial update with {default_language} language code"},
            },
        }

        url = self.get_comment_detail_url(self.comment.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "user": "invalid_user_id",
            "post": "invalid_post_id",
            "translations": {
                "invalid_lang_code": {
                    "content": "Translation for invalid language code",
                },
            },
        }

        url = self.get_comment_detail_url(self.comment.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_comment_detail_url(self.comment.pk)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(BlogComment.objects.filter(pk=self.comment.pk).exists())

    def test_destroy_invalid(self):
        invalid_comment_id = 9999
        url = self.get_comment_detail_url(invalid_comment_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def tearDown(self) -> None:
        BlogComment.objects.all().delete()
        BlogPost.objects.all().delete()
        BlogAuthor.objects.all().delete()
        User.objects.all().delete()
        super().tearDown()
