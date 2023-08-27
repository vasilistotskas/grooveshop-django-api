import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.test import override_settings
from django.test import TestCase

from blog.models.category import BlogCategory
from helpers.seed import get_or_create_default_image

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.memory.InMemoryStorage",
        },
    }
)
class BlogCategoryModelTestCase(TestCase):
    category: BlogCategory = None

    def setUp(self):
        # Create a sample BlogCategory instance for testing
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

    def test_fields(self):
        # Test if the fields are saved correctly
        self.assertEqual(self.category.slug, "sample-category")
        self.assertTrue(default_storage.exists(self.category.image.path))

    def test_verbose_names(self):
        # Test verbose names for fields
        self.assertEqual(
            BlogCategory._meta.get_field("slug").verbose_name,
            "slug",
        )
        self.assertEqual(
            BlogCategory._meta.get_field("image").verbose_name,
            "Image",
        )

    def test_meta_verbose_names(self):
        # Test verbose names from the Meta class
        self.assertEqual(
            BlogCategory._meta.verbose_name,
            "Blog Category",
        )
        self.assertEqual(
            BlogCategory._meta.verbose_name_plural,
            "Blog Categories",
        )

    def test_unicode_representation(self):
        # Test the __unicode__ method returns the translated name
        self.assertEqual(
            self.category.__unicode__(),
            self.category.safe_translation_getter("name"),
        )

    def test_translations(self):
        # Test if translations are saved correctly
        for language in languages:
            self.category.set_current_language(language)
            self.assertEqual(
                self.category.name,
                f"Category name in {language}",
            )
            self.assertEqual(
                self.category.description,
                f"Category description in {language}",
            )

    def test_str_representation(self):
        # Test the __str__ method returns the translated name
        self.assertEqual(
            str(self.category),
            self.category.safe_translation_getter("name"),
        )

    def test_get_category_posts_count(self):
        # Test if get_category_posts_count returns the correct count
        self.assertEqual(
            self.category.get_category_posts_count, 0
        )  # Assuming no posts related to this category

    def test_main_image_absolute_url(self):
        # Test if main_image_absolute_url returns the correct URL
        expected_url = settings.APP_BASE_URL + self.category.image.url
        self.assertEqual(self.category.main_image_absolute_url, expected_url)

    def test_main_image_filename(self):
        # Test if main_image_filename returns the correct filename
        expected_filename = os.path.basename(self.category.image.name)
        self.assertEqual(self.category.main_image_filename, expected_filename)

    def tearDown(self) -> None:
        super().tearDown()
        self.category.delete()
