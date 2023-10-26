import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.test import override_settings
from django.test import TestCase
from django.utils.translation import gettext_lazy as _

from helpers.seed import get_or_create_default_image
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
class SliderModelTestCase(TestCase):
    slider: Slider = None

    def setUp(self):
        image = get_or_create_default_image("uploads/sliders/no_photo.jpg")
        self.slider = Slider.objects.create(
            image=image,
        )
        for language in languages:
            self.slider.set_current_language(language)
            self.slider.name = f"Slider 1_{language}"
            self.slider.url = "https://www.example.com/"
            self.slider.title = f"Slider Title_{language}"
            self.slider.description = f"Slider Description_{language}"
            self.slider.save()
        self.slider.set_current_language(default_language)

    def test_fields(self):
        # Test if the fields are saved correctly
        self.assertTrue(default_storage.exists(self.slider.image.path))

    def test_verbose_names(self):
        # Test verbose names for fields
        self.assertEqual(
            Slider._meta.get_field("image").verbose_name,
            _("Image"),
        )

    def test_meta_verbose_names(self):
        # Test verbose names from the Meta class
        self.assertEqual(
            Slider._meta.verbose_name,
            _("Slider"),
        )
        self.assertEqual(
            Slider._meta.verbose_name_plural,
            _("Sliders"),
        )

    def test_unicode_representation(self):
        # Test the __unicode__ method returns the translated name
        self.assertEqual(
            self.slider.__unicode__(),
            self.slider.safe_translation_getter("name"),
        )

    def test_translations(self):
        # Test if translations are saved correctly
        for language in languages:
            self.slider.set_current_language(language)
            self.assertEqual(self.slider.name, f"Slider 1_{language}")
            self.assertEqual(self.slider.title, f"Slider Title_{language}")
            self.assertEqual(self.slider.description, f"Slider Description_{language}")

    def test_str_representation(self):
        # Test the __str__ method returns the translated name
        self.assertEqual(
            str(self.slider),
            self.slider.safe_translation_getter("name"),
        )

    def test_save(self):
        self.slider.image = get_or_create_default_image("uploads/sliders/no_photo.jpg")
        self.slider.save()
        self.assertTrue(default_storage.exists(self.slider.thumbnail.path))
        self.assertTrue(default_storage.exists(self.slider.image.path))

    def test_main_image_absolute_url(self):
        # Test if main_image_absolute_url returns the correct URL
        expected_url = settings.APP_BASE_URL + self.slider.image.url
        self.assertEqual(self.slider.main_image_absolute_url, expected_url)

    def test_main_image_filename(self):
        # Test if main_image_filename returns the correct filename
        expected_filename = os.path.basename(self.slider.image.name)
        self.assertEqual(self.slider.main_image_filename, expected_filename)

    def tearDown(self) -> None:
        super().tearDown()
        self.slider.delete()
