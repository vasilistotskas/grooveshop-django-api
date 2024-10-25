import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.test import TestCase

from country.factories import CountryFactory
from country.models import Country

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class CountryModelTestCase(TestCase):
    country: Country = None

    def setUp(self):
        self.country = CountryFactory(alpha_2="GR", alpha_3="GRC", iso_cc=300, phone_code=30, num_regions=0)

    def test_fields(self):
        self.assertEqual(self.country.alpha_2, "GR")
        self.assertEqual(self.country.alpha_3, "GRC")
        self.assertEqual(self.country.iso_cc, 300)
        self.assertEqual(self.country.phone_code, 30)
        self.assertTrue(default_storage.exists(self.country.image_flag.path))

    def test_unicode_representation(self):
        self.assertEqual(
            self.country.__unicode__(),
            self.country.safe_translation_getter("name"),
        )

    def test_str_representation(self):
        self.assertEqual(str(self.country), self.country.safe_translation_getter("name"))

    def test_get_ordering_queryset(self):
        queryset = self.country.get_ordering_queryset()
        self.assertTrue(queryset.exists())
        self.assertTrue(self.country in queryset)

    def test_main_image_path(self):
        expected_filename = f"media/uploads/country/{os.path.basename(self.country.image_flag.name)}"
        self.assertEqual(self.country.main_image_path, expected_filename)
