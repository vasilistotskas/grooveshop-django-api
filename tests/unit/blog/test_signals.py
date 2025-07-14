import asyncio
from unittest.mock import AsyncMock, patch
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from blog.models import BlogComment, BlogPost
from blog.signals import notify_comment_liked, notify_comment_liked_receiver
from notification.enum import NotificationKindEnum
from notification.models.notification import Notification
from notification.models.user import NotificationUser

User = get_user_model()


class TestNotifyCommentLiked(TestCase):
    def setUp(self):
        self.blog_post = BlogPost.objects.create(slug="test-post")

        self.comment_user = User.objects.create_user(
            email="commenter@example.com",
            username="commenter",
            password="testpass123",
        )
        self.liker_user = User.objects.create_user(
            email="liker@example.com", username="liker", password="testpass123"
        )

        self.comment = BlogComment.objects.create(
            post=self.blog_post, user=self.comment_user, approved=True
        )

    def _setup_async_mocks(
        self,
        mock_sync_to_async,
        mock_user_aget,
        mock_notification_acreate,
        mock_notification_user_acreate,
        user_return_value,
    ):
        def sync_to_async_side_effect(fn):
            async def wrapped_async(*args, **kwargs):
                if asyncio.iscoroutinefunction(fn):
                    return await fn(*args, **kwargs)
                return fn(*args, **kwargs)

            return wrapped_async

        mock_sync_to_async.side_effect = sync_to_async_side_effect

        async def async_aget_mock(*args, **kwargs):
            if callable(user_return_value):
                return user_return_value(*args, **kwargs)
            return user_return_value

        mock_user_aget.side_effect = async_aget_mock

        mock_notification = AsyncMock()
        mock_notification.title = ""
        mock_notification.message = ""
        mock_notification.asave = AsyncMock()

        async def async_notification_acreate(*args, **kwargs):
            return mock_notification

        mock_notification_acreate.side_effect = async_notification_acreate

        async def async_user_acreate_mock(*args, **kwargs):
            return AsyncMock()

        mock_notification_user_acreate.side_effect = async_user_acreate_mock

        return mock_notification

    async def test_notify_comment_liked_basic(self):
        with (
            patch("blog.signals.sync_to_async") as mock_sync_to_async,
            patch.object(User.objects, "aget") as mock_user_aget,
            patch.object(
                Notification.objects, "acreate"
            ) as mock_notification_acreate,
            patch.object(
                NotificationUser.objects, "acreate"
            ) as mock_notification_user_acreate,
        ):
            mock_notification = self._setup_async_mocks(
                mock_sync_to_async,
                mock_user_aget,
                mock_notification_acreate,
                mock_notification_user_acreate,
                self.liker_user,
            )

            await notify_comment_liked(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=False,
                pk_set={self.liker_user.id},
            )

            mock_notification_acreate.assert_called_once_with(
                kind=NotificationKindEnum.INFO,
                link=f"{settings.NUXT_BASE_URL}/blog/post/{self.blog_post.id}/{self.blog_post.slug}#blog-post-comments",
            )

            mock_notification_user_acreate.assert_called_once_with(
                user=self.comment_user, notification=mock_notification
            )

    async def test_notify_comment_liked_multiple_languages(self):
        with (
            patch("blog.signals.sync_to_async") as mock_sync_to_async,
            patch.object(User.objects, "aget") as mock_user_aget,
            patch.object(
                Notification.objects, "acreate"
            ) as mock_notification_acreate,
            patch.object(
                NotificationUser.objects, "acreate"
            ) as mock_notification_user_acreate,
            patch("blog.signals.languages", ["en", "el"]),
        ):
            mock_notification = self._setup_async_mocks(
                mock_sync_to_async,
                mock_user_aget,
                mock_notification_acreate,
                mock_notification_user_acreate,
                self.liker_user,
            )

            await notify_comment_liked(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=False,
                pk_set={self.liker_user.id},
            )

            self.assertEqual(
                mock_notification.set_current_language.call_count, 2
            )
            self.assertEqual(mock_notification.asave.call_count, 2)

    async def test_notify_comment_liked_english_content(self):
        with (
            patch("blog.signals.sync_to_async") as mock_sync_to_async,
            patch.object(User.objects, "aget") as mock_user_aget,
            patch.object(
                Notification.objects, "acreate"
            ) as mock_notification_acreate,
            patch.object(
                NotificationUser.objects, "acreate"
            ) as mock_notification_user_acreate,
            patch("blog.signals.languages", ["en"]),
        ):
            mock_notification = self._setup_async_mocks(
                mock_sync_to_async,
                mock_user_aget,
                mock_notification_acreate,
                mock_notification_user_acreate,
                self.liker_user,
            )

            await notify_comment_liked(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=False,
                pk_set={self.liker_user.id},
            )

            expected_url = f"{settings.NUXT_BASE_URL}/blog/post/{self.blog_post.id}/{self.blog_post.slug}#blog-post-comments"
            expected_title = f"<a href='{expected_url}'>Comment</a> Liked!"
            expected_message = f"Your comment was liked by {self.liker_user.username or self.liker_user.email}."

            self.assertEqual(mock_notification.title, expected_title)
            self.assertEqual(mock_notification.message, expected_message)

    async def test_notify_comment_liked_greek_content(self):
        with (
            patch("blog.signals.sync_to_async") as mock_sync_to_async,
            patch.object(User.objects, "aget") as mock_user_aget,
            patch.object(
                Notification.objects, "acreate"
            ) as mock_notification_acreate,
            patch.object(
                NotificationUser.objects, "acreate"
            ) as mock_notification_user_acreate,
            patch("blog.signals.languages", ["el"]),
        ):
            mock_notification = self._setup_async_mocks(
                mock_sync_to_async,
                mock_user_aget,
                mock_notification_acreate,
                mock_notification_user_acreate,
                self.liker_user,
            )

            await notify_comment_liked(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=False,
                pk_set={self.liker_user.id},
            )

            expected_url = f"{settings.NUXT_BASE_URL}/blog/post/{self.blog_post.id}/{self.blog_post.slug}#blog-post-comments"
            expected_title = (
                f"Το <a href='{expected_url}'>σχόλιο</a> σου πήρε like!"
            )
            expected_message = f"Το σχόλιο σου άρεσε στον χρήστη {self.liker_user.username or self.liker_user.email}."

            self.assertEqual(mock_notification.title, expected_title)
            self.assertEqual(mock_notification.message, expected_message)

    async def test_notify_comment_liked_user_without_username(self):
        with patch(
            "user.managers.account.UserNameGenerator"
        ) as mock_username_generator:
            mock_username_generator.return_value.generate_username.return_value = ""
            liker_no_username = await sync_to_async(User.objects.create_user)(
                email="no_username@example.com", password="testpass123"
            )

        with (
            patch("blog.signals.sync_to_async") as mock_sync_to_async,
            patch.object(User.objects, "aget") as mock_user_aget,
            patch.object(
                Notification.objects, "acreate"
            ) as mock_notification_acreate,
            patch.object(
                NotificationUser.objects, "acreate"
            ) as mock_notification_user_acreate,
            patch("blog.signals.languages", ["en"]),
        ):
            mock_notification = self._setup_async_mocks(
                mock_sync_to_async,
                mock_user_aget,
                mock_notification_acreate,
                mock_notification_user_acreate,
                liker_no_username,
            )

            await notify_comment_liked(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=False,
                pk_set={liker_no_username.id},
            )

            expected_message = (
                f"Your comment was liked by {liker_no_username.email}."
            )

            self.assertEqual(mock_notification.message, expected_message)

    async def test_notify_comment_liked_multiple_users(self):
        liker2 = await sync_to_async(User.objects.create_user)(
            email="liker2@example.com",
            username="liker2",
            password="testpass123",
        )

        with (
            patch("blog.signals.sync_to_async") as mock_sync_to_async,
            patch.object(User.objects, "aget") as mock_user_aget,
            patch.object(
                Notification.objects, "acreate"
            ) as mock_notification_acreate,
            patch.object(
                NotificationUser.objects, "acreate"
            ) as mock_notification_user_acreate,
        ):

            def aget_side_effect(id):
                if id == self.liker_user.id:
                    return self.liker_user
                elif id == liker2.id:
                    return liker2

            self._setup_async_mocks(
                mock_sync_to_async,
                mock_user_aget,
                mock_notification_acreate,
                mock_notification_user_acreate,
                aget_side_effect,
            )

            await notify_comment_liked(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=False,
                pk_set={self.liker_user.id, liker2.id},
            )

            self.assertEqual(mock_notification_acreate.call_count, 2)
            self.assertEqual(mock_notification_user_acreate.call_count, 2)

    async def test_notify_comment_liked_self_like(self):
        with (
            patch("blog.signals.sync_to_async") as mock_sync_to_async,
            patch.object(User.objects, "aget") as mock_user_aget,
            patch.object(
                Notification.objects, "acreate"
            ) as mock_notification_acreate,
            patch.object(
                NotificationUser.objects, "acreate"
            ) as mock_notification_user_acreate,
        ):
            self._setup_async_mocks(
                mock_sync_to_async,
                mock_user_aget,
                mock_notification_acreate,
                mock_notification_user_acreate,
                self.comment_user,
            )

            await notify_comment_liked(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=False,
                pk_set={self.comment_user.id},
            )

            mock_notification_acreate.assert_not_called()
            mock_notification_user_acreate.assert_not_called()

    def test_receiver_wrong_action(self):
        with patch("blog.signals.async_to_sync") as mock_async_to_sync:
            notify_comment_liked_receiver(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_remove",
                reverse=False,
                pk_set={self.liker_user.id},
            )
            mock_async_to_sync.assert_not_called()

    def test_receiver_reverse_action(self):
        with patch("blog.signals.async_to_sync") as mock_async_to_sync:
            notify_comment_liked_receiver(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=True,
                pk_set={self.liker_user.id},
            )
            mock_async_to_sync.assert_not_called()

    async def test_notify_comment_liked_empty_pk_set(self):
        with patch.object(
            Notification.objects, "acreate"
        ) as mock_notification_acreate:
            await notify_comment_liked(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=False,
                pk_set=set(),
            )
            mock_notification_acreate.assert_not_called()

    async def test_notify_comment_liked_no_comment_user(self):
        comment_no_user = await sync_to_async(BlogComment.objects.create)(
            post=self.blog_post, user=None, approved=True
        )

        with (
            patch("blog.signals.sync_to_async") as mock_sync_to_async,
            patch.object(User.objects, "aget") as mock_user_aget,
            patch.object(
                Notification.objects, "acreate"
            ) as mock_notification_acreate,
            patch.object(
                NotificationUser.objects, "acreate"
            ) as mock_notification_user_acreate,
        ):
            self._setup_async_mocks(
                mock_sync_to_async,
                mock_user_aget,
                mock_notification_acreate,
                mock_notification_user_acreate,
                self.liker_user,
            )

            await notify_comment_liked(
                sender=BlogComment.likes.through,
                instance=comment_no_user,
                action="post_add",
                reverse=False,
                pk_set={self.liker_user.id},
            )

            mock_notification_acreate.assert_not_called()
            mock_notification_user_acreate.assert_not_called()

    async def test_notify_comment_liked_url_generation(self):
        with (
            patch("blog.signals.sync_to_async") as mock_sync_to_async,
            patch.object(User.objects, "aget") as mock_user_aget,
            patch.object(
                Notification.objects, "acreate"
            ) as mock_notification_acreate,
            patch.object(
                NotificationUser.objects, "acreate"
            ) as mock_notification_user_acreate,
        ):
            self._setup_async_mocks(
                mock_sync_to_async,
                mock_user_aget,
                mock_notification_acreate,
                mock_notification_user_acreate,
                self.liker_user,
            )

            await notify_comment_liked(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=False,
                pk_set={self.liker_user.id},
            )

            expected_url = f"{settings.NUXT_BASE_URL}/blog/post/{self.blog_post.id}/{self.blog_post.slug}#blog-post-comments"

            mock_notification_acreate.assert_called_once_with(
                kind=NotificationKindEnum.INFO, link=expected_url
            )

    @override_settings(NUXT_BASE_URL="https://test.example.com")
    async def test_notify_comment_liked_custom_base_url(self):
        with (
            patch("blog.signals.sync_to_async") as mock_sync_to_async,
            patch.object(User.objects, "aget") as mock_user_aget,
            patch.object(
                Notification.objects, "acreate"
            ) as mock_notification_acreate,
            patch.object(
                NotificationUser.objects, "acreate"
            ) as mock_notification_user_acreate,
        ):
            self._setup_async_mocks(
                mock_sync_to_async,
                mock_user_aget,
                mock_notification_acreate,
                mock_notification_user_acreate,
                self.liker_user,
            )

            await notify_comment_liked(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=False,
                pk_set={self.liker_user.id},
            )

            expected_url = f"https://test.example.com/blog/post/{self.blog_post.id}/{self.blog_post.slug}#blog-post-comments"

            mock_notification_acreate.assert_called_once_with(
                kind=NotificationKindEnum.INFO, link=expected_url
            )

    def test_signal_registration(self):
        from django.db.models.signals import m2m_changed

        all_receivers = [
            r
            for sublist in m2m_changed._live_receivers(
                sender=BlogComment.likes.through
            )
            for r in sublist
        ]

        self.assertGreater(len(all_receivers), 0)

        handler_found = any(
            hasattr(r, "__name__")
            and r.__name__ == "notify_comment_liked_receiver"
            for r in all_receivers
        )
        self.assertTrue(
            handler_found,
            "notify_comment_liked_receiver signal handler not found",
        )

    def test_languages_configuration(self):
        from blog.signals import languages

        self.assertIsInstance(languages, list)
        for lang in languages:
            self.assertIsInstance(lang, str)
            self.assertGreater(len(lang), 0)

    async def test_notify_comment_liked_error_handling(self):
        with (
            patch("blog.signals.sync_to_async") as mock_sync_to_async,
            patch.object(User.objects, "aget") as mock_user_aget,
            patch.object(Notification.objects, "acreate"),
        ):

            def sync_to_async_side_effect(fn):
                async def wrapped_async(*args, **kwargs):
                    return fn(*args, **kwargs)

                return wrapped_async

            mock_sync_to_async.side_effect = sync_to_async_side_effect

            mock_user_aget.side_effect = User.DoesNotExist("Database error")

            try:
                await notify_comment_liked(
                    sender=BlogComment.likes.through,
                    instance=self.comment,
                    action="post_add",
                    reverse=False,
                    pk_set={self.liker_user.id},
                )
            except Exception as e:
                self.fail(
                    f"Signal handler should handle errors gracefully, but raised: {e}"
                )

    async def test_sync_to_async_usage(self):
        with (
            patch("blog.signals.sync_to_async") as mock_sync_to_async,
            patch.object(User.objects, "aget") as mock_user_aget,
            patch.object(
                Notification.objects, "acreate"
            ) as mock_notification_acreate,
            patch.object(
                NotificationUser.objects, "acreate"
            ) as mock_notification_user_acreate,
        ):
            self._setup_async_mocks(
                mock_sync_to_async,
                mock_user_aget,
                mock_notification_acreate,
                mock_notification_user_acreate,
                self.liker_user,
            )

            await notify_comment_liked(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=False,
                pk_set={self.liker_user.id},
            )

            self.assertGreater(mock_sync_to_async.call_count, 0)
            mock_user_aget.assert_called()
            mock_notification_acreate.assert_called()
            mock_notification_user_acreate.assert_called()


class TestBlogSignalsIntegration(TestCase):
    def setUp(self):
        self.blog_post = BlogPost.objects.create()
        self.user1 = User.objects.create_user(
            email="user1@example.com", username="user1", password="testpass123"
        )
        self.user2 = User.objects.create_user(
            email="user2@example.com", username="user2", password="testpass123"
        )
        self.comment = BlogComment.objects.create(
            post=self.blog_post, user=self.user1, approved=True
        )

    def test_signal_sender_configuration(self):
        self.assertEqual(
            BlogComment.likes.through._meta.label, "blog.BlogComment_likes"
        )

    def test_m2m_field_configuration(self):
        likes_field = BlogComment._meta.get_field("likes")
        self.assertEqual(likes_field.related_model, User)
        self.assertTrue(hasattr(likes_field.remote_field, "through"))

    async def test_notification_enum_usage(self):
        self.assertEqual(NotificationKindEnum.INFO.value, "INFO")
        valid_choices = [choice[0] for choice in NotificationKindEnum.choices]
        self.assertIn(NotificationKindEnum.INFO.value, valid_choices)


class TestBlogSignalsEdgeCases(TestCase):
    def setUp(self):
        self.blog_post = BlogPost.objects.create()
        self.user = User.objects.create_user(
            email="test@example.com",
            username="testuser",
            password="testpass123",
        )

    async def test_notify_with_none_pk_set(self):
        comment = await sync_to_async(BlogComment.objects.create)(
            post=self.blog_post, user=self.user, approved=True
        )

        with patch.object(
            Notification.objects, "acreate"
        ) as mock_notification_acreate:
            await notify_comment_liked(
                sender=BlogComment.likes.through,
                instance=comment,
                action="post_add",
                reverse=False,
                pk_set=None,
            )
            mock_notification_acreate.assert_not_called()

    async def test_notify_with_pre_add_action(self):
        comment = await sync_to_async(BlogComment.objects.create)(
            post=self.blog_post, user=self.user, approved=True
        )

        with patch("blog.signals.async_to_sync") as mock_async_to_sync:
            notify_comment_liked_receiver(
                sender=BlogComment.likes.through,
                instance=comment,
                action="pre_add",
                reverse=False,
                pk_set={self.user.id},
            )
            mock_async_to_sync.assert_not_called()

    async def test_async_error_isolation(self):
        comment = await sync_to_async(BlogComment.objects.create)(
            post=self.blog_post, user=self.user, approved=True
        )

        with patch("blog.signals.sync_to_async") as mock_sync_to_async:
            mock_sync_to_async.side_effect = Exception(
                "Async conversion failed"
            )

            try:
                await notify_comment_liked(
                    sender=BlogComment.likes.through,
                    instance=comment,
                    action="post_add",
                    reverse=False,
                    pk_set={self.user.id},
                )
            except Exception:
                self.fail("Signal should handle async errors gracefully")
