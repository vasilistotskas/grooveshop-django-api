import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.test import TestCase

from tip.enum.tip_enum import TipKindEnum
from tip.factories import TipFactory
from tip.models import Tip

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class TipModelTestCase(TestCase):
    tip: Tip = None

    def setUp(self):
        self.tip = TipFactory(
            kind=TipKindEnum.INFO,
            active=True,
        )

    def test_fields(self):
        self.assertEqual(self.tip.kind, TipKindEnum.INFO)
        self.assertTrue(self.tip.active)
        self.assertTrue(default_storage.exists(self.tip.icon.path))

    def test_unicode_representation(self):
        self.assertEqual(
            self.tip.__unicode__(),
            f"{self.tip.get_kind_display()}: {self.tip.safe_translation_getter('title')}",
        )

    def test_str_representation(self):
        self.assertEqual(
            str(self.tip),
            f"{self.tip.get_kind_display()}: {self.tip.safe_translation_getter('title')}",
        )

    def test_get_ordering_queryset(self):
        queryset = self.tip.get_ordering_queryset()
        self.assertTrue(queryset.exists())
        self.assertTrue(self.tip in queryset)

    def test_main_image_path(self):
        expected_filename = f"media/uploads/tip/{os.path.basename(self.tip.icon.name)}"
        self.assertEqual(self.tip.main_image_path, expected_filename)
