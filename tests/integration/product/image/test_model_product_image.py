import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.test import TestCase

from helpers.seed import get_or_create_default_image
from product.models.image import ProductImage
from product.models.product import Product

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.memory.InMemoryStorage",
        },
    }
)
class ProductImageModelTestCase(TestCase):
    product: Product = None
    product_image: ProductImage = None
    default_image: SimpleUploadedFile = None

    def setUp(self):
        self.product = Product.objects.create(
            name="Test Product",
            slug="test-product",
            price=20.00,
            active=True,
            stock=10,
        )

        self.default_image = get_or_create_default_image(
            "uploads/products/no_photo.jpg"
        )

        self.product_image = ProductImage.objects.create(
            product=self.product,
            image=self.default_image,
            is_main=True,
        )
        for language in languages:
            self.product_image.set_current_language(language)
            self.product_image.title = f"Sample Main Product Image ({language})"
            self.product_image.save()
        self.product_image.set_current_language(default_language)

    def test_fields(self):
        # Test if the fields are saved correctly
        self.assertEqual(self.product_image.product, self.product)
        self.assertTrue(self.product_image.is_main)
        self.assertTrue(default_storage.exists(self.product_image.image.path))

    def test_verbose_names(self):
        # Test verbose names for fields
        self.assertEqual(
            ProductImage._meta.get_field("product").verbose_name, "product"
        )
        self.assertEqual(ProductImage._meta.get_field("image").verbose_name, "Image")
        self.assertEqual(
            ProductImage._meta.get_field("thumbnail").verbose_name, "Thumbnail"
        )
        self.assertEqual(
            ProductImage._meta.get_field("is_main").verbose_name, "Is Main"
        )

    def test_meta_verbose_names(self):
        # Test verbose names from the Meta class
        self.assertEqual(ProductImage._meta.verbose_name, "Product Image")
        self.assertEqual(ProductImage._meta.verbose_name_plural, "Product Images")

    def test_unicode_representation(self):
        # Test unicode representation
        self.assertEqual(
            str(self.product_image), f"Sample Main Product Image ({default_language})"
        )

    def test_translations(self):
        # Test if translations are saved correctly
        for language in languages:
            self.product_image.set_current_language(language)
            self.assertEqual(
                self.product_image.title,
                f"Sample Main Product Image ({language})",
            )

    def test_str_representation(self):
        # Test the __str__ method returns the translated title
        self.assertEqual(
            self.product_image.__str__(),
            self.product_image.safe_translation_getter("title"),
        )

    def test_get_ordering_queryset(self):
        queryset = self.product_image.get_ordering_queryset()
        self.assertEqual(queryset.count(), 1)
        self.assertTrue(self.product_image in queryset)
        self.assertEqual(queryset.first(), self.product_image)

    def test_save(self):
        # Check that thumbnail is created and set after saving
        self.assertTrue(default_storage.exists(self.product_image.thumbnail.path))

    def test_main_image_absolute_url(self):
        # Test if main_image_absolute_url returns the correct URL
        expected_url = settings.APP_BASE_URL + self.product_image.image.url
        self.assertEqual(self.product_image.main_image_absolute_url, expected_url)

    def test_main_image_filename(self):
        # Test if main_image_filename returns the correct filename
        expected_filename = os.path.basename(self.product_image.image.url)
        self.assertEqual(self.product_image.main_image_filename, expected_filename)

    def tearDown(self) -> None:
        super().tearDown()
        self.product_image.delete()
        self.product.delete()
