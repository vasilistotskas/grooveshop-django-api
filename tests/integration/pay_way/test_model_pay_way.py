import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.test import override_settings
from django.test import TestCase
from djmoney.money import Money

from helpers.seed import get_or_create_default_image
from pay_way.enum.pay_way_enum import PayWayEnum
from pay_way.models import PayWay

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.memory.InMemoryStorage",
        },
    }
)
class PayWayModelTestCase(TestCase):
    pay_way: PayWay = None

    def setUp(self):
        image_icon = get_or_create_default_image("uploads/pay_way/no_photo.jpg")
        self.pay_way = PayWay.objects.create(
            active=True,
            cost=10.00,
            free_for_order_amount=100.00,
            icon=image_icon,
        )
        for language in languages:
            self.pay_way.set_current_language(language)
            self.pay_way.name = PayWayEnum.CREDIT_CARD
            self.pay_way.save()
        self.pay_way.set_current_language(default_language)

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

    def test_translations(self):
        for language in languages:
            self.pay_way.set_current_language(language)
            self.assertEqual(
                self.pay_way.name,
                PayWayEnum.CREDIT_CARD,
            )

    def test_str_representation(self):
        self.assertEqual(
            str(self.pay_way), self.pay_way.safe_translation_getter("name")
        )

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

    def tearDown(self) -> None:
        super().tearDown()
        self.pay_way.delete()
