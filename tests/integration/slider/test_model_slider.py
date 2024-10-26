import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.test import TestCase

from slider.factories import SliderFactory
from slider.models import Slider

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class SliderModelTestCase(TestCase):
    slider: Slider = None

    def setUp(self):
        self.slider = SliderFactory(num_slides=0)

    def test_fields(self):
        self.assertTrue(default_storage.exists(self.slider.image.path))

    def test_str_representation(self):
        self.assertEqual(
            str(self.slider),
            self.slider.safe_translation_getter("name"),
        )

    def test_save(self):
        self.slider.save()
        self.assertTrue(default_storage.exists(self.slider.image.path))

    def test_main_image_path(self):
        expected_filename = f"media/uploads/sliders/{os.path.basename(self.slider.image.name)}"
        self.assertEqual(self.slider.main_image_path, expected_filename)
