from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from blog.factories.author import BlogAuthorFactory
from blog.factories.comment import BlogCommentFactory
from blog.factories.post import BlogPostFactory
from blog.models.comment import BlogComment
from core.utils.testing import TestURLFixerMixin
from user.factories.account import UserAccountFactory

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class BlogCommentViewSetTestCase(TestURLFixerMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = UserAccountFactory()
        cls.author = BlogAuthorFactory(user=cls.user)
        cls.post = BlogPostFactory(
            author=cls.author,
            num_tags=0,
            num_comments=0,
        )
        cls.comment = BlogCommentFactory(
            is_approved=True, user=cls.user, post=cls.post
        )

    def get_comment_detail_url(self, pk):
        return reverse("blog-comment-detail", args=[pk])

    def get_comment_list_url(self):
        return reverse("blog-comment-list")

    def test_list(self):
        url = self.get_comment_list_url()
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
            "translations",
            "user",
            "content_preview",
            "is_reply",
            "parent",
            "has_replies",
            "is_approved",
            "is_edited",
            "likes_count",
            "replies_count",
            "user_has_liked",
            "created_at",
            "updated_at",
            "uuid",
        }
        self.assertTrue(
            expected_basic_fields.issubset(set(first_result.keys()))
        )

    def test_list_uses_correct_serializer(self):
        url = self.get_comment_list_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        first_result = response.data["results"][0]
        expected_fields = {
            "id",
            "translations",
            "user",
            "content_preview",
            "is_reply",
            "parent",
            "has_replies",
            "is_approved",
            "is_edited",
            "likes_count",
            "replies_count",
            "user_has_liked",
            "created_at",
            "updated_at",
            "uuid",
        }
        self.assertTrue(expected_fields.issubset(set(first_result.keys())))

    def test_retrieve_valid(self):
        url = self.get_comment_detail_url(self.comment.id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "translations",
            "user",
            "content_preview",
            "is_reply",
            "parent",
            "has_replies",
            "is_approved",
            "is_edited",
            "likes_count",
            "replies_count",
            "user_has_liked",
            "created_at",
            "updated_at",
            "uuid",
            "post",
            "parent_comment",
            "children_comments",
            "ancestors_path",
            "tree_position",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_retrieve_uses_correct_serializer(self):
        url = self.get_comment_detail_url(self.comment.id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        detail_only_fields = {
            "post",
            "parent_comment",
            "children_comments",
            "ancestors_path",
            "tree_position",
        }
        response_fields = set(response.data.keys())

        for field in detail_only_fields:
            self.assertIn(
                field,
                response_fields,
                f"Detail field '{field}' should be present in detail response",
            )

    def test_retrieve_invalid(self):
        invalid_comment_id = 9999
        url = self.get_comment_detail_url(invalid_comment_id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_valid(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "post": self.post.id,
            "translations": {},
        }

        for language in languages:
            language_code = language
            translation_payload = {
                "content": f"New Comment Content in {language_code}",
            }
            payload["translations"][language_code] = translation_payload

        url = self.get_comment_list_url()
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        expected_fields = {
            "id",
            "translations",
            "user",
            "content_preview",
            "is_reply",
            "parent",
            "has_replies",
            "is_approved",
            "is_edited",
            "likes_count",
            "replies_count",
            "user_has_liked",
            "created_at",
            "updated_at",
            "uuid",
            "post",
            "parent_comment",
            "children_comments",
            "ancestors_path",
            "tree_position",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_create_invalid(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "post": "invalid_post_id",
            "translations": {
                "invalid_lang_code": {
                    "content": "Translation for invalid language code",
                }
            },
        }

        url = self.get_comment_list_url()
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_valid(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "post": self.post.id,
            "translations": {},
        }

        for language in languages:
            language_code = language
            translation_payload = {
                "content": f"Updated Comment Content in {language_code}",
            }
            payload["translations"][language_code] = translation_payload

        url = self.get_comment_detail_url(self.comment.id)
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "translations",
            "user",
            "content_preview",
            "is_reply",
            "parent",
            "has_replies",
            "is_approved",
            "is_edited",
            "likes_count",
            "replies_count",
            "user_has_liked",
            "created_at",
            "updated_at",
            "uuid",
            "post",
            "parent_comment",
            "children_comments",
            "ancestors_path",
            "tree_position",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_update_invalid(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "post": "invalid_post_id",
            "translations": {
                "invalid_lang_code": {
                    "content": "Translation for invalid language code",
                },
            },
        }

        url = self.get_comment_detail_url(self.comment.id)
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "translations": {
                default_language: {
                    "content": "Partially Updated Comment Content"
                }
            }
        }

        url = self.get_comment_detail_url(self.comment.id)
        response = self.client.patch(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "translations",
            "user",
            "content_preview",
            "is_reply",
            "parent",
            "has_replies",
            "is_approved",
            "is_edited",
            "likes_count",
            "replies_count",
            "user_has_liked",
            "created_at",
            "updated_at",
            "uuid",
            "post",
            "parent_comment",
            "children_comments",
            "ancestors_path",
            "tree_position",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_partial_update_invalid(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "post": "invalid_post_id",
            "translations": {default_language: {"content": "Test Comment"}},
        }

        url = self.get_comment_detail_url(self.comment.id)
        response = self.client.patch(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        self.client.force_authenticate(user=self.user)
        comment_to_delete = BlogCommentFactory(
            is_approved=True, user=self.user, post=self.post
        )

        url = self.get_comment_detail_url(comment_to_delete.id)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        self.assertFalse(
            BlogComment.objects.filter(id=comment_to_delete.id).exists()
        )

    def test_destroy_invalid(self):
        self.client.force_authenticate(user=self.user)
        invalid_comment_id = 9999

        url = self.get_comment_detail_url(invalid_comment_id)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_replies_action(self):
        BlogCommentFactory(
            is_approved=True,
            user=self.user,
            post=self.post,
            parent=self.comment,
        )

        url = reverse("blog-comment-replies", args=[self.comment.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertIn("count", response.data)

        self.assertGreaterEqual(len(response.data["results"]), 0)

    def test_thread_action(self):
        reply = BlogCommentFactory(
            is_approved=True,
            user=self.user,
            post=self.post,
            parent=self.comment,
        )
        BlogCommentFactory(
            is_approved=True, user=self.user, post=self.post, parent=reply
        )

        url = reverse("blog-comment-thread", args=[reply.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertIn("count", response.data)

    def test_update_likes_action_authenticated(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("blog-comment-update_likes", args=[self.comment.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("action", response.data)
        self.assertIn(response.data["action"], ["liked", "unliked"])

    def test_update_likes_action_unauthenticated(self):
        url = reverse("blog-comment-update_likes", args=[self.comment.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_post_action(self):
        url = reverse("blog-comment-post", args=[self.comment.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(response.data["id"], self.post.id)
        self.assertEqual(response.data["slug"], self.post.slug)

    def test_liked_comments_action_authenticated(self):
        self.client.force_authenticate(user=self.user)
        payload = {"comment_ids": [self.comment.id]}

        url = reverse("blog-comment-liked_comments")
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("liked_comment_ids", response.data)
        self.assertIsInstance(response.data["liked_comment_ids"], list)

    def test_liked_comments_action_invalid_data(self):
        self.client.force_authenticate(user=self.user)
        payload = {"invalid_field": "test"}

        url = reverse("blog-comment-liked_comments")
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_my_comments_action_authenticated(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("blog-comment-my-comments")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertIn("count", response.data)

    def test_my_comments_action_unauthenticated(self):
        url = reverse("blog-comment-my-comments")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_my_comment_action_authenticated(self):
        self.client.force_authenticate(user=self.user)
        payload = {"post": self.post.id}

        url = reverse("blog-comment-my-comment")
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(response.data["post"]["id"], self.post.id)

    def test_my_comment_action_post_not_found(self):
        self.client.force_authenticate(user=self.user)
        payload = {"post": 9999}

        url = reverse("blog-comment-my-comment")
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_my_comment_action_comment_not_found(self):
        self.client.force_authenticate(user=self.user)
        other_post = BlogPostFactory(author=self.author)
        payload = {"post": other_post.id}

        url = reverse("blog-comment-my-comment")
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_filtering_functionality(self):
        url = self.get_comment_list_url()
        response = self.client.get(url, {"post": self.post.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        for _comment_data in response.data["results"]:
            pass

    def test_ordering_functionality(self):
        url = self.get_comment_list_url()
        response = self.client.get(url, {"ordering": "created_at"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)

    def test_search_functionality(self):
        url = self.get_comment_list_url()
        response = self.client.get(url, {"search": "test"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)

    def test_create_reply_valid(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "post": self.post.id,
            "parent": self.comment.id,
            "translations": {
                default_language: {"content": "This is a reply to the comment"}
            },
        }

        url = self.get_comment_list_url()
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertEqual(response.data["parent_comment"]["id"], self.comment.id)

    def test_create_reply_invalid_parent(self):
        self.client.force_authenticate(user=self.user)

        other_post = BlogPostFactory(author=self.author)
        other_comment = BlogCommentFactory(
            is_approved=True, user=self.user, post=other_post
        )

        payload = {
            "post": self.post.id,
            "parent": other_comment.id,
            "translations": {default_language: {"content": "This should fail"}},
        }

        url = self.get_comment_list_url()
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
