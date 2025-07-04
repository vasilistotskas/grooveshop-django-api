import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.test import TestCase

from blog.factories.category import BlogCategoryFactory

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class BlogCategoryModelTestCase(TestCase):
    def setUp(self):
        self.category = BlogCategoryFactory(slug="sample-category")

    def test_fields(self):
        self.assertEqual(self.category.slug, "sample-category")
        self.assertTrue(default_storage.exists(self.category.image.path))

    def test_str_representation(self):
        expected = " / ".join(
            [
                ancestor.safe_translation_getter(
                    "name", default="Unnamed", any_language=True
                )
                or "Unnamed"
                for ancestor in self.category.get_ancestors(include_self=True)
            ]
        )
        self.assertEqual(str(self.category), expected)

    def test_post_count(self):
        self.assertEqual(self.category.post_count, 0)

    def test_main_image_path(self):
        expected_filename = (
            f"media/uploads/blog/{os.path.basename(self.category.image.name)}"
        )
        self.assertEqual(self.category.main_image_path, expected_filename)
