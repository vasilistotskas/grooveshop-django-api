import os

from django.core.files.storage import default_storage
from django.db import connection
from django.test import TestCase

from country.factories import CountryFactory
from country.models import Country


class CountryModelTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # ISO 3166-1 reserves XA-XZ for user assignment, so no seeded
        # country can come back from the factory's get-or-create.
        cls.country = CountryFactory(
            alpha_2="XA", alpha_3="XAA", iso_cc=900, num_regions=0
        )

    def test_save_uppercases_the_codes(self):
        country = Country.objects.create(alpha_2="xb", alpha_3="xbb")

        country.refresh_from_db()
        self.assertEqual((country.alpha_2, country.alpha_3), ("XB", "XBB"))

    def test_factory_stores_the_flag_image(self):
        self.assertTrue(default_storage.exists(self.country.image_flag.path))

    def test_str_is_the_translated_name(self):
        self.assertEqual(
            str(self.country), self.country.safe_translation_getter("name")
        )

    def test_ordering_queryset_spans_every_country(self):
        self.assertQuerySetEqual(
            self.country.get_ordering_queryset(),
            Country.objects.all(),
            ordered=False,
        )

    def test_main_image_path(self):
        self.assertEqual(
            self.country.main_image_path,
            f"media/{connection.schema_name}/uploads/country/"
            f"{os.path.basename(self.country.image_flag.name)}",
        )
