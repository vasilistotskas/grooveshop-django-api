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
        # Create a sample Country instance for testing
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
            alpha_2=self.country,
        )
        for language in languages:
            self.region.set_current_language(language)
            self.region.name = f"Region {language}"
            self.region.save()
        self.region.set_current_language(default_language)

    def test_fields(self):
        # Test if the fields are saved correctly
        self.assertEqual(self.region.alpha, "GRC")
        self.assertEqual(self.region.alpha_2, self.country)

    def test_verbose_names(self):
        # Test verbose names for fields
        self.assertEqual(
            Region._meta.get_field("alpha").verbose_name,
            "Alpha",
        )
        self.assertEqual(
            Region._meta.get_field("alpha_2").verbose_name,
            "alpha 2",
        )

    def test_meta_verbose_names(self):
        # Test verbose names from the Meta class
        self.assertEqual(
            Region._meta.verbose_name,
            "Region",
        )
        self.assertEqual(
            Region._meta.verbose_name_plural,
            "Regions",
        )

    def test_unicode_representation(self):
        # Test the __unicode__ method returns the translated name
        self.assertEqual(
            self.region.__unicode__(),
            self.region.safe_translation_getter("name"),
        )

    def test_translations(self):
        # Test if translations are saved correctly
        for language in languages:
            self.region.set_current_language(language)
            self.assertEqual(self.region.name, f"Region {language}")

    def test_str_representation(self):
        # Test the __str__ method returns the translated name
        self.assertEqual(str(self.region), self.region.safe_translation_getter("name"))

    def test_get_ordering_queryset(self):
        # Test if get_ordering_queryset returns Region queryset
        queryset = self.region.get_ordering_queryset()
        self.assertTrue(queryset.exists())
        self.assertTrue(self.region in queryset)

    def tearDown(self) -> None:
        super().tearDown()
        self.region.delete()
        self.country.delete()
