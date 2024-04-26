import os

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.test import TestCase

from helpers.seed import get_or_create_default_image
from settings import BASE_DIR


@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.memory.InMemoryStorage",
        },
    }
)
class TestGetOrCreateDefaultImage(TestCase):
    existing_image: SimpleUploadedFile = None
    existing_image_path: str = "test_image.jpg"
    existing_image_content: bytes = b"test_image_content"
    non_existing_image: SimpleUploadedFile = None
    non_existing_image_name: str = "non_existing_image.jpg"
    default_image: SimpleUploadedFile = None
    default_image_path: str = "static/images/no_photo.jpg"

    def setUp(self):
        default_storage.save(
            self.existing_image_path,
            SimpleUploadedFile(self.existing_image_path, self.existing_image_content),
        )

        self.existing_image = get_or_create_default_image(self.existing_image_path)
        self.non_existing_image = get_or_create_default_image(
            self.non_existing_image_name
        )
        self.default_image = get_or_create_default_image(self.default_image_path)

    def test_existing_image(self):
        self.assertEqual(self.existing_image.read(), self.existing_image_content)

    def test_non_existing_image(self):
        default_image_full_path = os.path.join(BASE_DIR, self.default_image_path)

        image_content = self.non_existing_image.read()
        with open(default_image_full_path, "rb") as f:
            default_image_content = f.read()

        self.assertEqual(image_content, default_image_content)

    def test_default_image_already_exists(self):
        self.assertTrue(default_storage.exists(self.default_image_path))

    def tearDown(self) -> None:
        super().tearDown()
        pass
