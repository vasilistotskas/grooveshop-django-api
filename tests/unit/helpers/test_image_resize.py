from io import BytesIO

from django.core.files import File
from django.test import TestCase
from PIL import Image

from helpers.image_resize import make_thumbnail


class TestMakeThumbnail(TestCase):
    def test_make_thumbnail(self):
        # Create a test image using PIL
        img = Image.new("RGB", size=(300, 200), color="red")
        img_io = BytesIO()
        img.save(img_io, format="JPEG")
        img_io.seek(0)
        test_image = File(img_io, name="test_image.jpg")

        # Create a thumbnail using the make_thumbnail function
        thumbnail = make_thumbnail(test_image, (100, 100))

        # Ensure the thumbnail is a File object
        self.assertIsInstance(thumbnail, File)

        # Ensure the thumbnail is a JPEG
        self.assertEqual(thumbnail.name.split(".")[-1], "jpg")

        # Ensure the thumbnail is 100x100
        thumbnail_img = Image.open(thumbnail)
        self.assertEqual(thumbnail_img.size, (100, 67))

    def tearDown(self) -> None:
        super().tearDown()
        pass
