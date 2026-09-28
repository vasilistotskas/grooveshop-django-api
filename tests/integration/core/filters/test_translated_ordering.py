"""Ordering through a parler translation table returns each row once.

Countries are seeded with el/en/de names, so a list ordered by
``translations__name`` is exactly the case that used to fan every row out
once per language: ``/country`` served 248 rows of which only 83 were
distinct, and paginating it never reached most countries.
"""

from collections import Counter

from django.urls import reverse
from rest_framework.test import APITestCase

from core.filters.translations import translated_value
from country.models import Country
from region.models import Region
from tests.utils import TestURLFixerMixin


class TranslatedOrderingTestCase(TestURLFixerMixin, APITestCase):
    def _all_alpha_2(self, params: dict) -> list[str]:
        codes: list[str] = []
        page = 1
        while True:
            data = self.client.get(
                reverse("country-list"),
                {**params, "page": page, "pageSize": 100},
            ).json()
            codes += [row["alpha2"] for row in data["results"]]
            if not data["links"]["next"]:
                return codes
            page += 1

    def test_default_ordering_lists_every_country_once(self):
        codes = self._all_alpha_2({})

        self.assertEqual(
            [code for code, n in Counter(codes).items() if n > 1], []
        )
        self.assertEqual(
            set(codes),
            set(Country.objects.values_list("alpha_2", flat=True)),
        )

    def test_ordering_by_name_follows_the_requested_language(self):
        # The database's own collation decides the order; filtering the
        # join to one language keeps it at one row per country.
        english = list(
            Country.objects.filter(translations__language_code="en")
            .order_by("translations__name")
            .values_list("alpha_2", flat=True)
        )
        first_page = self.client.get(
            reverse("country-list"),
            {"ordering": "translations_Name", "languageCode": "en"},
        ).json()["results"]

        self.assertEqual(
            [row["alpha2"] for row in first_page],
            english[: len(first_page)],
        )

    def test_a_key_across_a_relation_orders_without_fan_out(self):
        ordered = Region.objects.order_by(
            translated_value(Region, "country__translations__name", "en", "el")
        )

        self.assertEqual(ordered.count(), Region.objects.count())
        self.assertEqual(
            len(list(ordered.values_list("alpha", flat=True))),
            Region.objects.count(),
        )
