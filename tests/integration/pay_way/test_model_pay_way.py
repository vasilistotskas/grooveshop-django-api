import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.test import TestCase
from djmoney.money import Money

from pay_way.factories import PayWayFactory
from pay_way.models import PayWay

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class PayWayModelTestCase(TestCase):
    pay_way: PayWay = None

    def setUp(self):
        self.pay_way = PayWayFactory(
            active=True,
            cost=10.00,
            free_for_order_amount=100.00,
        )

    def test_fields(self):
        self.assertTrue(self.pay_way.active)
        self.assertEqual(self.pay_way.cost, Money("10.0", settings.DEFAULT_CURRENCY))
        self.assertEqual(
            self.pay_way.free_for_order_amount,
            Money("100.0", settings.DEFAULT_CURRENCY),
        )
        self.assertTrue(default_storage.exists(self.pay_way.icon.path))

    def test_unicode_representation(self):
        self.assertEqual(
            self.pay_way.__unicode__(),
            self.pay_way.safe_translation_getter("name"),
        )

    def test_str_representation(self):
        self.assertEqual(str(self.pay_way), self.pay_way.safe_translation_getter("name"))

    def test_get_ordering_queryset(self):
        queryset = self.pay_way.get_ordering_queryset()
        self.assertTrue(queryset.exists())
        self.assertTrue(self.pay_way in queryset)

    def test_icon_absolute_url(self):
        icon_absolute_url = self.pay_way.icon_absolute_url
        self.assertTrue(icon_absolute_url.startswith(settings.APP_BASE_URL))
        self.assertTrue(icon_absolute_url.endswith(self.pay_way.icon.url))

    def test_icon_filename(self):
        icon_filename = self.pay_way.icon_filename
        self.assertEqual(icon_filename, os.path.basename(self.pay_way.icon.name))
