from __future__ import annotations

import os

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from app.settings import BASE_DIR
from blog.models.category import BlogCategory


class BlogCategoryTestCase(TestCase):
    image: str | SimpleUploadedFile = ""
    category: BlogCategory

    def setUp(self):
        self.category = BlogCategory.objects.create(
            name="name", slug="slug", description="description", image=self.image
        )

    def test___str__(self):
        category = BlogCategory.objects.get(slug="slug")
        self.assertEqual(str(category), category.name)

    def test_main_image_absolute_url(self):
        category = BlogCategory.objects.get(slug="slug")
        image: str = ""
        if category.image and hasattr(category.image, "url"):
            image = settings.APP_BASE_URL + category.image.url
        self.assertEqual(category.main_image_absolute_url, image)

    def test_get_main_image_filename(self):
        category = BlogCategory.objects.get(slug="slug")
        image: str = ""
        if category.image is not None:
            image = os.path.basename(category.image.name)
        self.assertEqual(category.main_image_filename, image)


class WithImage(BlogCategoryTestCase):
    image: str | SimpleUploadedFile = "uploads/blog/no_photo.jpg"

    def setUp(self):
        super().setUp()
        image_path = os.path.join(BASE_DIR, "files/images") + "/no_photo.jpg"
        with open(image_path, "rb") as image:
            self.image = SimpleUploadedFile(
                name="no_photo.jpg", content=image.read(), content_type="image/jpeg"
            )
        self.category.image = self.image
        self.category.save()


class WithoutImage(BlogCategoryTestCase):
    image: str | SimpleUploadedFile = ""
