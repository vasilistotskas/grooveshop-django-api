from django.test import TestCase

from vat.models import Vat


class VatTestCase(TestCase):
    def setUp(self):
        Vat.objects.create(value=0.0)
        Vat.objects.create(value=14.0)
        Vat.objects.create(value=21.0)

    def test_get_highest_vat_value(self):
        highest_vat = Vat.objects.all().order_by("-value").first()
        self.assertEqual(Vat.get_highest_vat_value(), highest_vat)
