import os
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import default_storage
from django.test import TestCase
from django.utils.timezone import now
from djmoney.money import Money

from slider.factories import SlideFactory
from slider.factories import SliderFactory
from slider.models import Slide
from slider.models import Slider

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class SlideModelTestCase(TestCase):
    slide: Slide = None
    slider: Slider = None

    def setUp(self):
        date_start = now()
        self.slider = SliderFactory(num_slides=0)
        self.slide = SlideFactory(
            discount=0.0,
            show_button=True,
            date_start=date_start,
            date_end=date_start + timedelta(days=30),
            slider=self.slider,
        )

    def test_fields(self):
        self.assertEqual(self.slide.discount, Money("0.0", settings.DEFAULT_CURRENCY))
        self.assertTrue(self.slide.show_button)
        self.assertTrue(default_storage.exists(self.slide.image.path))

    def test_str_representation(self):
        self.assertEqual(
            str(self.slide),
            f"{self.slide.safe_translation_getter('title', any_language=True)} in {self.slider}",
        )

    def test_save(self):
        self.slide.save()
        self.assertTrue(default_storage.exists(self.slide.image.path))

    def test_main_image_path(self):
        expected_filename = f"media/uploads/slides/{os.path.basename(self.slide.image.name)}"
        self.assertEqual(self.slide.main_image_path, expected_filename)
