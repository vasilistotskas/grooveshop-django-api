from django.test import TestCase

from vat.models import Vat


class VatModelTestCase(TestCase):
    vat: Vat = None

    def setUp(self):
        self.vat = Vat.objects.create(
            value=21.0,
        )

    def test_fields(self):
        self.assertEqual(self.vat.value, 21.0)

    def test_verbose_names(self):
        self.assertEqual(
            Vat._meta.get_field("value").verbose_name,
            "Value",
        )

    def test_meta_verbose_names(self):
        self.assertEqual(
            Vat._meta.verbose_name,
            "Vat",
        )
        self.assertEqual(
            Vat._meta.verbose_name_plural,
            "Vats",
        )

    def test_str_representation(self):
        self.assertEqual(str(self.vat), f"{self.vat.value}% VAT")

    def test_get_highest_vat_value(self):
        Vat.objects.create(value=10.0)
        Vat.objects.create(value=20.0)
        Vat.objects.create(value=30.0)
        self.assertEqual(Vat.get_highest_vat_value(), 30.0)

    def tearDown(self) -> None:
        super().tearDown()
        self.vat.delete()
