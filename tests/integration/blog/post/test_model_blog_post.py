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
        self.user = UserAccountFactory()
        self.author = BlogAuthorFactory(user=self.user)
        self.category = BlogCategoryFactory(slug="sample-category")

        for language in languages:
            self.category.set_current_language(language)
            self.category.name = f"Category name in {language}"
            self.category.description = f"Category description in {language}"
            self.category.save()
        self.category.set_current_language(default_language)

        self.post = BlogPostFactory(
            slug="test-post",
            author=self.author,
            category=self.category,
            status="DRAFT",
            featured=True,
            view_count=0,
        )

        for language in languages:
            self.post.set_current_language(language)
            self.post.title = f"Title in {language}"
            self.post.subtitle = f"Subtitle in {language}"
            self.post.body = f"Body in {language}"
            self.post.save()
        self.post.set_current_language(default_language)

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

    def test_translations(self):
        for language in languages:
            self.post.set_current_language(language)
            self.assertEqual(
                self.post.title,
                f"Title in {language}",
            )
            self.assertEqual(
                self.post.subtitle,
                f"Subtitle in {language}",
            )
            self.assertEqual(
                self.post.body,
                f"Body in {language}",
            )

    def test_str_representation(self):
        title = self.post.safe_translation_getter("title", any_language=True) or "Untitled"
        author_name = self.post.author.user.email if self.author else "Unknown"
        self.assertEqual(
            str(self.post),
            f"{title} by {author_name}",
        )

    def test_main_image_absolute_url(self):
        expected_url = settings.APP_BASE_URL + self.post.image.url
        self.assertEqual(self.post.main_image_absolute_url, expected_url)

    def test_main_image_filename(self):
        expected_filename = os.path.basename(self.post.image.name)
        self.assertEqual(self.post.main_image_filename, expected_filename)

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
