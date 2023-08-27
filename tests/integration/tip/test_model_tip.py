import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.test import override_settings
from django.test import TestCase

from helpers.seed import get_or_create_default_image
from tip.enum.tip_enum import TipKindEnum
from tip.models import Tip

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.memory.InMemoryStorage",
        },
    }
)
class TipModelTestCase(TestCase):
    tip: Tip = None
    default_icon: str = None

    def setUp(self):
        # Create a sample Tip instance for testing
        self.default_icon = get_or_create_default_image("uploads/tip/no_photo.jpg")
        self.tip = Tip.objects.create(
            kind=TipKindEnum.INFO,
            icon=self.default_icon,
            active=True,
        )
        for language in languages:
            self.tip.set_current_language(language)
            self.tip.title = f"Info_{language}"
            self.tip.content = f"Info content_{language}"
            self.tip.url = f"https://www.google.com_{language}"
            self.tip.save()
        self.tip.set_current_language(default_language)

    def test_fields(self):
        # Test if the fields are saved correctly
        self.assertEqual(self.tip.kind, TipKindEnum.INFO)
        self.assertTrue(self.tip.active)
        self.assertTrue(default_storage.exists(self.tip.icon.path))

    def test_verbose_names(self):
        # Test verbose names for fields
        self.assertEqual(
            Tip._meta.get_field("kind").verbose_name,
            "Kind",
        )
        self.assertEqual(
            Tip._meta.get_field("icon").verbose_name,
            "Icon",
        )
        self.assertEqual(
            Tip._meta.get_field("active").verbose_name,
            "Active",
        )

    def test_meta_verbose_names(self):
        # Test verbose names for model
        self.assertEqual(
            Tip._meta.verbose_name,
            "Tip",
        )
        self.assertEqual(
            Tip._meta.verbose_name_plural,
            "Tips",
        )

    def test_unicode_representation(self):
        # Test the __unicode__ method returns the translated name
        self.assertEqual(
            self.tip.__unicode__(),
            self.tip.safe_translation_getter("title"),
        )

    def test_translations(self):
        # Test if translations are saved correctly
        for language in languages:
            self.tip.set_current_language(language)
            self.assertEqual(self.tip.title, f"Info_{language}")
            self.assertEqual(self.tip.content, f"Info content_{language}")
            self.assertEqual(self.tip.url, f"https://www.google.com_{language}")

    def test_str_representation(self):
        # Test the __str__ method returns the translated name
        self.assertEqual(str(self.tip), self.tip.safe_translation_getter("title"))

    def test_get_ordering_queryset(self):
        # Test if get_ordering_queryset returns Tip queryset
        queryset = self.tip.get_ordering_queryset()
        self.assertTrue(queryset.exists())
        self.assertTrue(self.tip in queryset)

    def test_image_tag(self):
        # Test the image_tag method
        self.assertEqual(
            self.tip.image_tag,
            f'<img src="{self.tip.icon.url}" height="50"/>',
        )

    def test_main_image_absolute_url(self):
        # Test if main_image_absolute_url returns the correct URL
        expected_url = settings.APP_BASE_URL + self.tip.icon.url
        self.assertEqual(self.tip.main_image_absolute_url, expected_url)

    def test_main_image_filename(self):
        # Test if main_image_filename returns the correct filename
        expected_filename = os.path.basename(self.tip.icon.name)
        self.assertEqual(self.tip.main_image_filename, expected_filename)

    def tearDown(self) -> None:
        super().tearDown()
        self.tip.delete()
