from io import BytesIO

from django.core.files import File
from django.test import TestCase
from PIL import Image

from helpers.image_resize import make_thumbnail


class TestMakeThumbnail(TestCase):
    def test_make_thumbnail(self):
        img = Image.new("RGB", size=(300, 200), color="red")
        img_io = BytesIO()
        img.save(img_io, format="JPEG")
        img_io.seek(0)
        test_image = File(img_io, name="test_image.jpg")

        thumbnail = make_thumbnail(test_image, (100, 100))

        self.assertIsInstance(thumbnail, File)

        self.assertEqual(thumbnail.name.split(".")[-1], "jpg")

        thumbnail_img = Image.open(thumbnail)
        self.assertEqual(thumbnail_img.size, (100, 67))

    def tearDown(self) -> None:
        super().tearDown()
        pass
