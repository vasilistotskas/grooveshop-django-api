"""``PayWay.key`` is the language-independent identity of a pay way.

It used to live in the parler translation table, seeded for ``el`` only,
so a storefront reading ``translations[locale].name`` got nothing on
``/en``. On the row it is the same in every locale.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse
from django.utils import translation
from rest_framework import status

from pay_way.enum.pay_way import PayWayEnum
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from pay_way.models import PayWay


class PayWayKeyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        PayWay.objects.all().delete()
        cls.cod = PayWayFactory(
            key=PayWayEnum.PAY_ON_DELIVERY,
            provider_code="cash_on_delivery",
            settlement=PaySettlement.COURIER_CASH,
        )
        cls.card = PayWayFactory(
            key=PayWayEnum.CREDIT_CARD,
            provider_code="viva_wallet",
            settlement=PaySettlement.ONLINE,
        )

    def test_the_key_is_the_same_in_every_language(self):
        for language in ("el", "en", "de"):
            with translation.override(language):
                self.cod.set_current_language(language)
                self.assertEqual(
                    PayWay.objects.get(pk=self.cod.pk).key,
                    PayWayEnum.PAY_ON_DELIVERY,
                )

    def test_a_pay_way_with_no_el_translation_still_has_its_key(self):
        """The seeded-``el``-only shape that blanked ``/en``."""
        self.cod.translations.filter(language_code="el").delete()

        response = self.client.get(reverse("payway-list"), {"language": "en"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        keys = {row["key"] for row in response.json()["results"]}
        self.assertIn(PayWayEnum.PAY_ON_DELIVERY.value, keys)

    def test_the_list_serializer_emits_the_key(self):
        response = self.client.get(reverse("payway-list"))

        by_id = {row["id"]: row for row in response.json()["results"]}
        self.assertEqual(
            by_id[self.cod.pk]["key"], PayWayEnum.PAY_ON_DELIVERY.value
        )

    def test_no_translated_name_is_emitted(self):
        response = self.client.get(reverse("payway-list"))

        for row in response.json()["results"]:
            for language_copy in row["translations"].values():
                self.assertNotIn("name", language_copy)

    def test_the_key_filter_matches_exactly(self):
        response = self.client.get(
            reverse("payway-list"), {"key": PayWayEnum.PAY_ON_DELIVERY.value}
        )

        ids = [row["id"] for row in response.json()["results"]]
        self.assertEqual(ids, [self.cod.pk])

    def test_the_label_follows_the_key(self):
        with translation.override("en"):
            self.assertEqual(self.card.display_name, "Credit Card")
