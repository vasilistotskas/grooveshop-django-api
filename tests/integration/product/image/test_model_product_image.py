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
        for language in languages:
            self.product_image.set_current_language(language)
            self.product_image.title = f"Sample Main Product Image ({language})"
            self.product_image.save()
        self.product_image.set_current_language(default_language)

    def test_fields(self):
        self.assertEqual(self.product_image.product, self.product)
        self.assertTrue(self.product_image.is_main)
        self.assertTrue(default_storage.exists(self.product_image.image.path))

    def test_unicode_representation(self):
        product_name = self.product.safe_translation_getter("name", any_language=True)
        main_status = "Main" if self.product_image.is_main else "Secondary"

        self.assertEqual(
            self.product_image.__unicode__(),
            f"{product_name} Image ({main_status})",
        )

    def test_translations(self):
        for language in languages:
            self.product_image.set_current_language(language)
            self.assertEqual(
                self.product_image.title,
                f"Sample Main Product Image ({language})",
            )

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

    def test_main_image_absolute_url(self):
        expected_url = settings.APP_BASE_URL + self.product_image.image.url
        self.assertEqual(self.product_image.main_image_absolute_url, expected_url)

    def test_main_image_filename(self):
        expected_filename = os.path.basename(self.product_image.image.url)
        self.assertEqual(self.product_image.main_image_filename, expected_filename)

    def tearDown(self) -> None:
        Product.objects.all().delete()
        ProductImage.objects.all().delete()
        super().tearDown()
