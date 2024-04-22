from django.conf import settings
from django.test import override_settings
from django.test import TestCase

from country.models import Country
from helpers.seed import get_or_create_default_image
from region.models import Region

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.memory.InMemoryStorage",
        },
    }
)
class RegionModelTestCase(TestCase):
    region: Region = None
    country: Country = None

    def setUp(self):
        image_flag = get_or_create_default_image("uploads/region/no_photo.jpg")
        self.country = Country.objects.create(
            alpha_2="GR",
            alpha_3="GRC",
            iso_cc=300,
            phone_code=30,
            image_flag=image_flag,
        )
        self.region = Region.objects.create(
            alpha="GRC",
            country=self.country,
        )
        for language in languages:
            self.region.set_current_language(language)
            self.region.name = f"Region {language}"
            self.region.save()
        self.region.set_current_language(default_language)

    def test_fields(self):
        self.assertEqual(self.region.alpha, "GRC")
        self.assertEqual(self.region.country, self.country)

    def test_unicode_representation(self):
        country_name = self.country.safe_translation_getter("name", any_language=True)
        region_name = self.region.safe_translation_getter("name", any_language=True)
        self.assertEqual(self.region.__unicode__(), f"{region_name}, {country_name}")

    def test_translations(self):
        for language in languages:
            self.region.set_current_language(language)
            self.assertEqual(self.region.name, f"Region {language}")

    def test_str_representation(self):
        country_name = self.country.safe_translation_getter("name", any_language=True)
        region_name = self.region.safe_translation_getter("name", any_language=True)
        self.assertEqual(str(self.region), f"{region_name}, {country_name}")

    def test_get_ordering_queryset(self):
        queryset = self.region.get_ordering_queryset()
        self.assertTrue(queryset.exists())
        self.assertTrue(self.region in queryset)

    def tearDown(self) -> None:
        super().tearDown()
        self.region.delete()
        self.country.delete()
