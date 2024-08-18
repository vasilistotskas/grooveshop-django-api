from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from blog.factories.author import BlogAuthorFactory
from blog.factories.comment import BlogCommentFactory
from blog.factories.post import BlogPostFactory
from blog.models.author import BlogAuthor
from blog.models.comment import BlogComment
from blog.models.post import BlogPost
from user.factories.account import UserAccountFactory

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE

User = get_user_model()


class BlogCommentModelTestCase(TestCase):
    comment: BlogComment = None
    user: User = None
    author: BlogAuthor = None
    post: BlogPost = None

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.author = BlogAuthorFactory(user=self.user)
        self.post = BlogPostFactory(author=self.author, num_tags=0, num_comments=0)
        self.comment = BlogCommentFactory(is_approved=True, user=self.user, post=self.post)

    def test_fields(self):
        self.assertTrue(self.comment.is_approved)
        self.assertEqual(self.comment.user, self.user)
        self.assertEqual(self.comment.post, self.post)

    def test_unicode_representation(self):
        content_snippet = self.comment.safe_translation_getter("content", any_language=True)[:50] + "..."
        self.assertEqual(
            self.comment.__unicode__(),
            f"Comment by {self.user.full_name}: {content_snippet}",
        )

    def test_likes_count(self):
        other_user = UserAccountFactory(num_addresses=0)

        self.assertEqual(self.comment.likes_count, 0)

        self.comment.likes.add(other_user)

        self.assertEqual(self.comment.likes_count, 1)

    def tearDown(self) -> None:
        BlogComment.objects.all().delete()
        BlogPost.objects.all().delete()
        BlogAuthor.objects.all().delete()
        User.objects.all().delete()
        super().tearDown()
