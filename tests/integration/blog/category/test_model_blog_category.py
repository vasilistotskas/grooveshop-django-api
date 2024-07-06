import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.test import TestCase

from blog.factories.category import BlogCategoryFactory
from blog.models.category import BlogCategory

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class BlogCategoryModelTestCase(TestCase):
    category: BlogCategory = None

    def setUp(self):
        self.category = BlogCategoryFactory(slug="sample-category")
        for language in languages:
            self.category.set_current_language(language)
            self.category.name = f"Category name in {language}"
            self.category.description = f"Category description in {language}"
            self.category.save()
        self.category.set_current_language(default_language)

    def test_fields(self):
        self.assertEqual(self.category.slug, "sample-category")
        self.assertTrue(default_storage.exists(self.category.image.path))

    def test_unicode_representation(self):
        self.assertEqual(
            self.category.__unicode__(),
            self.category.safe_translation_getter("name"),
        )

    def test_translations(self):
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
        self.assertEqual(
            str(self.category),
            self.category.safe_translation_getter("name"),
        )

    def test_post_count(self):
        self.assertEqual(self.category.post_count, 0)

    def test_main_image_absolute_url(self):
        expected_url = settings.APP_BASE_URL + self.category.image.url
        self.assertEqual(self.category.main_image_absolute_url, expected_url)

    def test_main_image_filename(self):
        expected_filename = os.path.basename(self.category.image.name)
        self.assertEqual(self.category.main_image_filename, expected_filename)

    def tearDown(self) -> None:
        BlogCategory.objects.all().delete()
        super().tearDown()
