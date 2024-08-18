from django.conf import settings
from django.test import TestCase

from country.factories import CountryFactory
from country.models import Country
from region.factories import RegionFactory
from region.models import Region

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class RegionModelTestCase(TestCase):
    region: Region = None
    country: Country = None

    def setUp(self):
        self.country = CountryFactory(num_regions=0)
        self.region = RegionFactory(
            alpha="GRC",
            country=self.country,
        )

    def test_fields(self):
        self.assertEqual(self.region.alpha, "GRC")
        self.assertEqual(self.region.country, self.country)

    def test_unicode_representation(self):
        country_name = self.country.safe_translation_getter("name", any_language=True)
        region_name = self.region.safe_translation_getter("name", any_language=True)
        self.assertEqual(self.region.__unicode__(), f"{region_name}, {country_name}")

    def test_str_representation(self):
        country_name = self.country.safe_translation_getter("name", any_language=True)
        region_name = self.region.safe_translation_getter("name", any_language=True)
        self.assertEqual(str(self.region), f"{region_name}, {country_name}")

    def test_get_ordering_queryset(self):
        queryset = self.region.get_ordering_queryset()
        self.assertTrue(queryset.exists())
        self.assertTrue(self.region in queryset)

    def tearDown(self) -> None:
        Region.objects.all().delete()
        Country.objects.all().delete()
        super().tearDown()
