from django.test import TestCase

from vat.factories import VatFactory


class VatModelTestCase(TestCase):
    def setUp(self):
        self.vat = VatFactory(value=21.0)

    def test_fields(self):
        self.assertEqual(self.vat.value, 21.0)

    def test_str_representation(self):
        self.assertEqual(str(self.vat), f"{self.vat.value}% VAT")
