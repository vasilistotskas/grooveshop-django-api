import os
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import default_storage
from django.test import override_settings
from django.test import TestCase
from django.utils.timezone import now
from djmoney.money import Money

from helpers.seed import get_or_create_default_image
from slider.models import Slide
from slider.models import Slider

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.memory.InMemoryStorage",
        },
    }
)
class SlideModelTestCase(TestCase):
    slide: Slide = None
    slider: Slider = None

    def setUp(self):
        image = get_or_create_default_image("uploads/slides/no_photo.jpg")
        date_start = now()
        self.slider = Slider.objects.create()
        self.slide = Slide.objects.create(
            discount=0.0,
            show_button=True,
            image=image,
            date_start=date_start,
            date_end=date_start + timedelta(days=30),
            slider=self.slider,
        )
        for language in languages:
            self.slide.set_current_language(language)
            self.slide.name = f"Slide 1_{language}"
            self.slide.url = "https://www.example.com/"
            self.slide.title = f"Slide Title_{language}"
            self.slide.description = f"Slide Description_{language}"
            self.slide.button_label = f"Slide Button Label_{language}"
            self.slide.save()
        self.slide.set_current_language(default_language)

    def test_fields(self):
        self.assertEqual(self.slide.discount, Money("0.0", settings.DEFAULT_CURRENCY))
        self.assertTrue(self.slide.show_button)
        self.assertTrue(default_storage.exists(self.slide.image.path))

    def test_unicode_representation(self):
        self.assertEqual(
            self.slide.__unicode__(),
            f"{self.slide.safe_translation_getter('title', any_language=True)} in {self.slider}",
        )

    def test_translations(self):
        for language in languages:
            self.slide.set_current_language(language)
            self.assertEqual(self.slide.name, f"Slide 1_{language}")
            self.assertEqual(self.slide.title, f"Slide Title_{language}")
            self.assertEqual(self.slide.description, f"Slide Description_{language}")
            self.assertEqual(self.slide.button_label, f"Slide Button Label_{language}")

    def test_str_representation(self):
        self.assertEqual(
            str(self.slide),
            f"{self.slide.safe_translation_getter('title', any_language=True)} in {self.slider}",
        )

    def test_save(self):
        self.slide.image = get_or_create_default_image("uploads/slides/no_photo.jpg")
        self.slide.save()
        self.assertTrue(default_storage.exists(self.slide.image.path))

    def test_main_image_absolute_url(self):
        expected_url = settings.APP_BASE_URL + self.slide.image.url
        self.assertEqual(self.slide.main_image_absolute_url, expected_url)

    def test_main_image_filename(self):
        expected_filename = os.path.basename(self.slide.image.name)
        self.assertEqual(self.slide.main_image_filename, expected_filename)

    def tearDown(self) -> None:
        super().tearDown()
        self.slide.delete()
        self.slider.delete()
