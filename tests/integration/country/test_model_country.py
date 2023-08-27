import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.test import override_settings
from django.test import TestCase

from country.models import Country
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
class CountryModelTestCase(TestCase):
    country: Country = None

    def setUp(self):
        # Create a sample Country instance for testing
        image_flag = get_or_create_default_image("uploads/country/no_photo.jpg")
        self.country = Country.objects.create(
            alpha_2="GR",
            alpha_3="GRC",
            iso_cc=300,
            phone_code=30,
            image_flag=image_flag,
        )
        for language in languages:
            self.country.set_current_language(language)
            self.country.name = f"Greece_{language}"
            self.country.save()
        self.country.set_current_language(default_language)

    def test_fields(self):
        # Test if the fields are saved correctly
        self.assertEqual(self.country.alpha_2, "GR")
        self.assertEqual(self.country.alpha_3, "GRC")
        self.assertEqual(self.country.iso_cc, 300)
        self.assertEqual(self.country.phone_code, 30)
        self.assertTrue(default_storage.exists(self.country.image_flag.path))

    def test_verbose_names(self):
        # Test verbose names for fields
        self.assertEqual(
            Country._meta.get_field("alpha_2").verbose_name,
            "Country Code Alpha 2",
        )
        self.assertEqual(
            Country._meta.get_field("alpha_3").verbose_name,
            "Country Code Alpha 3",
        )
        self.assertEqual(
            Country._meta.get_field("iso_cc").verbose_name,
            "ISO Country Code",
        )
        self.assertEqual(
            Country._meta.get_field("phone_code").verbose_name,
            "Phone Code",
        )
        self.assertEqual(
            Country._meta.get_field("image_flag").verbose_name,
            "Image Flag",
        )

    def test_meta_verbose_names(self):
        # Test verbose names from the Meta class
        self.assertEqual(
            Country._meta.verbose_name,
            "Country",
        )
        self.assertEqual(
            Country._meta.verbose_name_plural,
            "Countries",
        )

    def test_unicode_representation(self):
        # Test the __unicode__ method returns the translated name
        self.assertEqual(
            self.country.__unicode__(),
            self.country.safe_translation_getter("name"),
        )

    def test_translations(self):
        # Test if translations are saved correctly
        for language in languages:
            self.country.set_current_language(language)
            self.assertEqual(
                self.country.name,
                f"Greece_{language}",
            )

    def test_str_representation(self):
        # Test the __str__ method returns the translated name
        self.assertEqual(
            str(self.country), self.country.safe_translation_getter("name")
        )

    def test_get_ordering_queryset(self):
        # Test if get_ordering_queryset returns Country queryset
        queryset = self.country.get_ordering_queryset()
        self.assertTrue(queryset.exists())
        self.assertTrue(self.country in queryset)

    def test_main_image_absolute_url(self):
        # Test if main_image_absolute_url returns the correct URL
        expected_url = settings.APP_BASE_URL + self.country.image_flag.url
        self.assertEqual(self.country.main_image_absolute_url, expected_url)

    def test_main_image_filename(self):
        # Test if main_image_filename returns the correct filename
        expected_filename = os.path.basename(self.country.image_flag.name)
        self.assertEqual(self.country.main_image_filename, expected_filename)

    def tearDown(self) -> None:
        super().tearDown()
        self.country.delete()
