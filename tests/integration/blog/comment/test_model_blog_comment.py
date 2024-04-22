from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from blog.models.author import BlogAuthor
from blog.models.comment import BlogComment
from blog.models.post import BlogPost

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE

User = get_user_model()


class BlogCommentModelTestCase(TestCase):
    comment: BlogComment = None
    user: User = None
    author: BlogAuthor = None
    post: BlogPost = None

    def setUp(self):
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        self.author = BlogAuthor.objects.create(user=self.user)
        self.post = BlogPost.objects.create(title="Test Post", author=self.author)
        self.comment = BlogComment.objects.create(
            is_approved=True, user=self.user, post=self.post
        )
        for language in languages:
            self.comment.set_current_language(language)
            self.comment.content = f"Comment Content in {language}"
            self.comment.save()
        self.comment.set_current_language(default_language)

    def test_fields(self):
        self.assertTrue(self.comment.is_approved)
        self.assertEqual(self.comment.user, self.user)
        self.assertEqual(self.comment.post, self.post)

    def test_unicode_representation(self):
        content_snippet = (
            self.comment.safe_translation_getter("content", any_language=True)[:50]
            + "..."
        )
        self.assertEqual(
            self.comment.__unicode__(),
            f"Comment by {self.user.full_name}: {content_snippet}",
        )

    def test_translations(self):
        for language in languages:
            self.comment.set_current_language(language)
            self.assertEqual(
                self.comment.content,
                f"Comment Content in {language}",
            )

    def test_str_representation(self):
        content_snippet = self.comment.safe_translation_getter(
            "content", any_language=True
        )[:50]
        self.assertEqual(
            str(self.comment), f"Comment by {self.user.full_name}: {content_snippet}"
        )

    def test_likes_count(self):
        other_user = User.objects.create_user(
            email="testuser2@example.com", password="testpassword"
        )

        self.assertEqual(self.comment.likes_count, 0)

        self.comment.likes.add(other_user)

        self.assertEqual(self.comment.likes_count, 1)

    def tearDown(self) -> None:
        super().tearDown()
        self.comment.delete()
        self.post.delete()
        self.author.delete()
        self.user.delete()
