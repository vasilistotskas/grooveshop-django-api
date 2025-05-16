from django.test import TestCase

from vat.factories import VatFactory
from vat.models import Vat


class VatModelTestCase(TestCase):
    vat: Vat = None

    def setUp(self):
        self.vat = VatFactory(value=21.0)

    def test_fields(self):
        self.assertEqual(self.vat.value, 21.0)

    def test_str_representation(self):
        self.assertEqual(str(self.vat), f"{self.vat.value}% VAT")

    def test_get_highest_vat_value(self):
        VatFactory(value=10.0)
        VatFactory(value=20.0)
        VatFactory(value=30.0)
        self.assertEqual(Vat.get_highest_vat_value(), 30.0)
