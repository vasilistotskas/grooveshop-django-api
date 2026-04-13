from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from blog.models import BlogComment, BlogPost
from blog.signals import notify_comment_liked_receiver
from notification.enum import NotificationKindEnum

User = get_user_model()


class TestNotifyCommentLikedReceiver(TestCase):
    def setUp(self):
        self.blog_post = BlogPost.objects.create(slug="test-post")

        self.comment_user = User.objects.create_user(
            email="commenter@example.com",
            username="commenter",
            password="testpass123",
        )
        self.liker_user = User.objects.create_user(
            email="liker@example.com",
            username="liker",
            password="testpass123",
        )

        self.comment = BlogComment.objects.create(
            post=self.blog_post, user=self.comment_user, approved=True
        )

    def test_receiver_wrong_action(self):
        with patch("blog.tasks.notify_comment_liked_task") as mock_task:
            notify_comment_liked_receiver(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_remove",
                reverse=False,
                pk_set={self.liker_user.id},
            )
            mock_task.delay.assert_not_called()

    def test_receiver_reverse_action(self):
        with patch("blog.tasks.notify_comment_liked_task") as mock_task:
            notify_comment_liked_receiver(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=True,
                pk_set={self.liker_user.id},
            )
            mock_task.delay.assert_not_called()

    def test_receiver_empty_pk_set(self):
        with patch("blog.tasks.notify_comment_liked_task") as mock_task:
            notify_comment_liked_receiver(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=False,
                pk_set=set(),
            )
            mock_task.delay.assert_not_called()

    def test_receiver_none_pk_set(self):
        with patch("blog.tasks.notify_comment_liked_task") as mock_task:
            notify_comment_liked_receiver(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=False,
                pk_set=None,
            )
            mock_task.delay.assert_not_called()

    def test_receiver_dispatches_task(self):
        with patch("blog.tasks.notify_comment_liked_task") as mock_task:
            notify_comment_liked_receiver(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=False,
                pk_set={self.liker_user.id},
            )
            mock_task.delay.assert_called_once()

    def test_receiver_no_comment_user(self):
        """Receiver should not dispatch task if comment has no user."""
        comment_no_user = BlogComment.objects.create(
            post=self.blog_post, user=None, approved=True
        )
        with patch("blog.tasks.notify_comment_liked_task") as mock_task:
            notify_comment_liked_receiver(
                sender=BlogComment.likes.through,
                instance=comment_no_user,
                action="post_add",
                reverse=False,
                pk_set={self.liker_user.id},
            )
            mock_task.delay.assert_not_called()

    def test_receiver_no_post(self):
        """Receiver should not dispatch task if comment has no post."""
        comment_no_post = BlogComment(
            post=None, user=self.comment_user, approved=True
        )
        with patch("blog.tasks.notify_comment_liked_task") as mock_task:
            notify_comment_liked_receiver(
                sender=BlogComment.likes.through,
                instance=comment_no_post,
                action="post_add",
                reverse=False,
                pk_set={self.liker_user.id},
            )
            mock_task.delay.assert_not_called()

    def test_receiver_task_called_with_correct_args(self):
        """Receiver passes correct arguments to the Celery task."""
        with patch("blog.tasks.notify_comment_liked_task") as mock_task:
            notify_comment_liked_receiver(
                sender=BlogComment.likes.through,
                instance=self.comment,
                action="post_add",
                reverse=False,
                pk_set={self.liker_user.id},
            )
            call_kwargs = mock_task.delay.call_args.kwargs
            assert call_kwargs["comment_id"] == self.comment.id
            assert call_kwargs["comment_owner_id"] == self.comment_user.id
            assert list(call_kwargs["liker_user_ids"]) == [self.liker_user.id]


class TestBlogSignalsRegistration(TestCase):
    def setUp(self):
        self.blog_post = BlogPost.objects.create()
        self.user1 = User.objects.create_user(
            email="user1@example.com", username="user1", password="testpass123"
        )
        self.comment = BlogComment.objects.create(
            post=self.blog_post, user=self.user1, approved=True
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

    def test_signal_sender_configuration(self):
        self.assertEqual(
            BlogComment.likes.through._meta.label, "blog.BlogComment_likes"
        )

    def test_m2m_field_configuration(self):
        likes_field = BlogComment._meta.get_field("likes")
        self.assertEqual(likes_field.related_model, User)
        self.assertTrue(hasattr(likes_field.remote_field, "through"))

    def test_notification_enum_usage(self):
        self.assertEqual(NotificationKindEnum.INFO.value, "INFO")
        valid_choices = [choice[0] for choice in NotificationKindEnum.choices]
        self.assertIn(NotificationKindEnum.INFO.value, valid_choices)
