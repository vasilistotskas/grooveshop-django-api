import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import TestCase

from blog.factories.author import BlogAuthorFactory
from blog.factories.category import BlogCategoryFactory
from blog.factories.post import BlogPostFactory
from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.post import BlogPost
from user.factories.account import UserAccountFactory

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class BlogPostModelTestCase(TestCase):
    post: BlogPost = None
    user: User = None
    author: BlogAuthor = None
    category: BlogCategory = None

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.author = BlogAuthorFactory(user=self.user)
        self.category = BlogCategoryFactory(slug="sample-category")
        self.post = BlogPostFactory(
            slug="test-post",
            author=self.author,
            category=self.category,
            status="DRAFT",
            featured=True,
            view_count=0,
            num_tags=0,
            num_comments=0,
        )

    def test_fields(self):
        self.assertEqual(self.post.slug, "test-post")
        self.assertEqual(self.post.status, "DRAFT")
        self.assertEqual(self.post.featured, True)
        self.assertEqual(self.post.view_count, 0)
        self.assertTrue(default_storage.exists(self.post.image.path))

    def test_unicode_representation(self):
        title = self.post.safe_translation_getter("title", any_language=True) or "Untitled"
        author_name = self.post.author.user.email if self.author else "Unknown"
        self.assertEqual(
            self.post.__unicode__(),
            f"{title} by {author_name}",
        )

    def test_str_representation(self):
        title = self.post.safe_translation_getter("title", any_language=True) or "Untitled"
        author_name = self.post.author.user.email if self.author else "Unknown"
        self.assertEqual(
            str(self.post),
            f"{title} by {author_name}",
        )

    def test_main_image_path(self):
        expected_filename = f"media/uploads/blog/{os.path.basename(self.post.image.name)}"
        self.assertEqual(self.post.main_image_path, expected_filename)

    def test_likes_count(self):
        self.assertEqual(self.post.likes_count, 0)

    def test_comments_count(self):
        self.assertEqual(self.post.comments_count, 0)

    def test_tags_count(self):
        self.assertEqual(self.post.tags_count, 0)

    def tearDown(self) -> None:
        BlogPost.objects.all().delete()
        BlogCategory.objects.all().delete()
        BlogAuthor.objects.all().delete()
        User.objects.all().delete()
        super().tearDown()
