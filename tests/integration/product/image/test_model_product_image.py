import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.test import TestCase

from product.factories.image import ProductImageFactory
from product.factories.product import ProductFactory
from product.models.image import ProductImage
from product.models.product import Product

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class ProductImageModelTestCase(TestCase):
    product: Product = None
    product_image: ProductImage = None

    def setUp(self):
        self.product = ProductFactory(num_images=0, num_reviews=0)
        self.product_image = ProductImageFactory(
            product=self.product,
            is_main=True,
        )

    def test_fields(self):
        self.assertEqual(self.product_image.product, self.product)
        self.assertTrue(self.product_image.is_main)
        self.assertTrue(default_storage.exists(self.product_image.image.path))

    def test_str_representation(self):
        product_name = self.product.safe_translation_getter("name", any_language=True)
        main_status = "Main" if self.product_image.is_main else "Secondary"

        self.assertEqual(
            str(self.product_image),
            f"{product_name} Image ({main_status})",
        )

    def test_get_ordering_queryset(self):
        queryset = self.product_image.get_ordering_queryset()
        self.assertEqual(queryset.count(), 1)
        self.assertTrue(self.product_image in queryset)
        self.assertEqual(queryset.first(), self.product_image)

    def test_save(self):
        self.assertTrue(default_storage.exists(self.product_image.thumbnail.path))

    def test_main_image_path(self):
        expected_filename = f"media/uploads/products/{os.path.basename(self.product_image.image.name)}"
        self.assertEqual(self.product_image.main_image_path, expected_filename)
