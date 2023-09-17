import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import override_settings
from django.test import TestCase

from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.post import BlogPost
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
class BlogPostModelTestCase(TestCase):
    post: BlogPost = None
    user: User = None
    author: BlogAuthor = None
    category: BlogCategory = None

    def setUp(self):
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )

        image_post = get_or_create_default_image("uploads/blog/no_photo.jpg")
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

        self.post = BlogPost.objects.create(
            slug="test-post",
            author=self.author,
            category=self.category,
            image=image_post,
            status="draft",
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
        # Test if the fields are saved correctly
        self.assertEqual(self.post.slug, "test-post")
        self.assertEqual(self.post.status, "draft")
        self.assertEqual(self.post.featured, True)
        self.assertEqual(self.post.view_count, 0)
        self.assertTrue(default_storage.exists(self.post.image.path))

    def test_verbose_names(self):
        # Test verbose names for fields
        self.assertEqual(
            BlogPost._meta.get_field("slug").verbose_name,
            "slug",
        )
        self.assertEqual(
            BlogPost._meta.get_field("image").verbose_name,
            "Image",
        )
        self.assertEqual(
            BlogPost._meta.get_field("status").verbose_name,
            "Status",
        )
        self.assertEqual(
            BlogPost._meta.get_field("featured").verbose_name,
            "Featured",
        )
        self.assertEqual(
            BlogPost._meta.get_field("view_count").verbose_name,
            "View Count",
        )

    def test_meta_verbose_names(self):
        # Test verbose names from the Meta class
        self.assertEqual(
            BlogPost._meta.verbose_name,
            "Blog Post",
        )
        self.assertEqual(
            BlogPost._meta.verbose_name_plural,
            "Blog Posts",
        )

    def test_unicode_representation(self):
        # Test the __unicode__ method returns the translated name
        self.assertEqual(
            self.post.__unicode__(),
            self.post.safe_translation_getter("title"),
        )

    def test_translations(self):
        # Test if translations are saved correctly
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
        # Test the __str__ method returns the translated title
        self.assertEqual(
            str(self.post),
            self.post.safe_translation_getter("title"),
        )

    def test_main_image_absolute_url(self):
        # Test if main_image_absolute_url returns the correct URL
        expected_url = settings.APP_BASE_URL + self.post.image.url
        self.assertEqual(self.post.main_image_absolute_url, expected_url)

    def test_main_image_filename(self):
        # Test if main_image_filename returns the correct filename
        expected_filename = os.path.basename(self.post.image.name)
        self.assertEqual(self.post.main_image_filename, expected_filename)

    def test_number_of_likes(self):
        self.assertEqual(self.post.number_of_likes, 0)

    def test_number_of_comments(self):
        self.assertEqual(self.post.number_of_comments, 0)

    def test_post_tags_count(self):
        self.assertEqual(self.post.post_tags_count, 0)

    def tearDown(self) -> None:
        super().tearDown()
        self.post.delete()
        self.author.delete()
        self.user.delete()
        self.category.delete()
