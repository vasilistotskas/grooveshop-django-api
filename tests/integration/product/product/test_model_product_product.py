import os
from decimal import Decimal
from typing import List

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.test import TestCase
from django.utils.html import format_html
from djmoney.money import Money

from helpers.seed import get_or_create_default_image
from product.models.category import ProductCategory
from product.models.favourite import ProductFavourite
from product.models.image import ProductImage
from product.models.product import Product
from product.models.review import ProductReview
from vat.models import Vat

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.memory.InMemoryStorage",
        },
    }
)
class ProductModelTestCase(TestCase):
    product: Product = None
    user: User = None
    category: ProductCategory = None
    vat: Vat = None
    product_images: List[ProductImage] = []
    product_reviews: List[ProductReview] = []
    product_favourite: ProductFavourite = None

    def setUp(self):
        # Create a sample user for testing
        self.user = User.objects.create_user(
            email="test@test.com", password="test12345@!"
        )
        # Create a sample Category instance for testing
        self.category = ProductCategory.objects.create(
            slug="sample-category",
        )
        for language in languages:
            self.category.set_current_language(language)
            self.category.name = f"Sample Category ({language})"
            self.category.description = (
                f"This is a sample category description ({language})."
            )
            self.category.save()
        self.category.set_current_language(default_language)

        # Create a sample VAT instance for testing
        self.vat = Vat.objects.create(
            value=Decimal("24.0"),
        )

        # Create a sample Product instance for testing
        self.product = Product.objects.create(
            product_code="P123456",
            category=self.category,
            slug="sample-product",
            price=Decimal("100.00"),
            active=True,
            stock=10,
            discount_percent=Decimal("50.0"),
            vat=self.vat,
            hits=10,
            weight=Decimal("5.00"),
        )
        for language in languages:
            self.product.set_current_language(language)
            self.product.name = f"Sample Product ({language})"
            self.product.description = (
                f"This is a sample product description ({language})."
            )
            self.product.save()
        self.product.set_current_language(default_language)

        # Create a sample main ProductImage instance for testing
        image = get_or_create_default_image("uploads/products/no_photo.jpg")
        main_product_image = ProductImage.objects.create(
            product=self.product,
            image=image,
            is_main=True,
        )
        for language in languages:
            main_product_image.set_current_language(language)
            main_product_image.title = f"Sample Main Product Image ({language})"
            main_product_image.save()
        main_product_image.set_current_language(default_language)
        self.product_images.append(main_product_image)

        # Create a sample non-main ProductImage instance for testing
        non_main_product_image = ProductImage.objects.create(
            product=self.product,
            image=image,
            is_main=False,
        )
        for language in languages:
            non_main_product_image.set_current_language(language)
            non_main_product_image.title = f"Sample Non-Main Product Image ({language})"
            non_main_product_image.save()
        non_main_product_image.set_current_language(default_language)
        self.product_images.append(non_main_product_image)

        # Create a sample ProductFavourite instance for testing
        self.product_favourite = ProductFavourite.objects.create(
            product=self.product,
            user=self.user,
        )

        user_2 = User.objects.create_user(
            email="test2@test.com", password="test12345@!"
        )

        # Create a sample ProductReview with status "True" instance for testing
        product_review_status_true = ProductReview.objects.create(
            product=self.product,
            user=self.user,
            rate=5,
            status="True",
            comment="Sample Product Review Comment",
        )
        self.product_reviews.append(product_review_status_true)

        # Create a sample ProductReview with status "False" instance for testing
        product_review_status_false = ProductReview.objects.create(
            product=self.product,
            user=user_2,
            rate=5,
            status="False",
            comment="Sample Product Review Comment",
        )
        self.product_reviews.append(product_review_status_false)

    def test_save_method(self):
        # Test if the save method calculates fields correctly
        self.product.price = Decimal("100.00")
        self.product.discount_percent = Decimal("50.0")
        self.product.vat = self.vat

        self.product.save()
        # Refresh the product instance from the database
        self.product.refresh_from_db()

        expected_discount_value = (
            self.product.price * self.product.discount_percent
        ) / 100
        expected_vat_value = (self.product.price * self.vat.value) / 100
        expected_final_price = (
            self.product.price + expected_vat_value - expected_discount_value
        )
        expected_price_save_percent = (
            expected_discount_value / self.product.price
        ) * 100

        self.assertEqual(self.product.discount_value, expected_discount_value)
        self.assertEqual(self.product.vat_value, expected_vat_value)
        self.assertEqual(self.product.final_price, expected_final_price)
        self.assertEqual(self.product.price_save_percent, expected_price_save_percent)

    def test_fields(self):
        # Test if the fields are saved correctly
        self.assertEqual(self.product.product_code, "P123456")
        self.assertEqual(self.product.slug, "sample-product")
        self.assertEqual(self.product.price, Money("100.00", settings.DEFAULT_CURRENCY))
        self.assertEqual(self.product.active, True)
        self.assertEqual(self.product.stock, 10)
        self.assertEqual(self.product.discount_percent, Decimal("50.0"))
        self.assertEqual(self.product.vat, self.vat)
        self.assertEqual(self.product.hits, 10)
        self.assertEqual(self.product.weight, Decimal("5.00"))

    def test_verbose_names(self):
        # Test verbose names for fields
        self.assertEqual(
            Product._meta.get_field("product_code").verbose_name,
            "Product Code",
        )
        self.assertEqual(
            Product._meta.get_field("category").verbose_name,
            "category",
        )
        self.assertEqual(
            Product._meta.get_field("slug").verbose_name,
            "Slug",
        )
        self.assertEqual(
            Product._meta.get_field("price").verbose_name,
            "Price",
        )
        self.assertEqual(
            Product._meta.get_field("active").verbose_name,
            "Active",
        )
        self.assertEqual(
            Product._meta.get_field("stock").verbose_name,
            "Stock",
        )
        self.assertEqual(
            Product._meta.get_field("discount_percent").verbose_name,
            "Discount Percent",
        )
        self.assertEqual(
            Product._meta.get_field("vat").verbose_name,
            "vat",
        )
        self.assertEqual(
            Product._meta.get_field("hits").verbose_name,
            "Hits",
        )
        self.assertEqual(
            Product._meta.get_field("weight").verbose_name,
            "Weight (kg)",
        )
        self.assertEqual(
            Product._meta.get_field("final_price").verbose_name,
            "Final Price",
        )
        self.assertEqual(
            Product._meta.get_field("discount_value").verbose_name,
            "Discount Value",
        )
        self.assertEqual(
            Product._meta.get_field("price_save_percent").verbose_name,
            "Price Save Percent",
        )

    def test_meta_verbose_names(self):
        # Test verbose names from the Meta class
        self.assertEqual(
            Product._meta.verbose_name,
            "Product",
        )
        self.assertEqual(
            Product._meta.verbose_name_plural,
            "Products",
        )

    def test_unicode_representation(self):
        # Test the __unicode__ method returns the translated name
        self.assertEqual(
            self.product.__unicode__(),
            self.product.safe_translation_getter("name"),
        )

    def test_translations(self):
        # Test if translations are saved correctly
        for language in languages:
            self.product.set_current_language(language)
            self.assertEqual(
                self.product.name,
                f"Sample Product ({language})",
            )
            self.assertEqual(
                self.product.description,
                f"This is a sample product description ({language}).",
            )

    def test_str_representation(self):
        # Test the __str__ method returns the translated name
        self.assertEqual(
            str(self.product), self.product.safe_translation_getter("name")
        )

    def test_likes_counter(self):
        # Test likes counter
        self.assertEqual(self.product.likes_counter, 1)

    def test_review_average(self):
        # Test review average
        self.assertEqual(self.product.review_average, 5)

    def test_review_counter(self):
        # Test review counter, should be 1 because we created a review with status True
        # and one with status False, we only count the ones with status True
        self.assertEqual(self.product.review_counter, 1)

    def test_vat_percent(self):
        # Test vat percent
        self.assertEqual(self.product.vat_percent, self.vat.value)

    def test_vat_value(self):
        # Test vat value
        expected_vat_value = (self.product.price.amount * self.vat.value) / 100
        self.assertEqual(self.product.vat_value.amount, expected_vat_value)

    def test_main_image_absolute_url(self):
        # Test if main_image_absolute_url returns the correct URL
        main_image = self.product.product_images.filter(is_main=True).first()
        expected_url = settings.APP_BASE_URL + main_image.image.url
        self.assertEqual(self.product.main_image_absolute_url, expected_url)

    def test_main_image_filename(self):
        # Test if main_image_filename returns the correct filename
        main_image = self.product.product_images.filter(is_main=True).first()
        expected_filename = os.path.basename(main_image.image.name)
        self.assertEqual(self.product.main_image_filename, expected_filename)

    def test_colored_stock_property(self):
        self.product.stock = 5
        self.assertEqual(
            self.product.colored_stock,
            format_html('<span style="color: #1bff00;">{}</span>', self.product.stock),
        )

        self.product.stock = 0
        self.assertEqual(
            self.product.colored_stock,
            format_html('<span style="color: #ff0000;">{}</span>', self.product.stock),
        )

    def test_absolute_url_property(self):
        expected_absolute_url = f"/{self.product.id}/{self.product.slug}"
        self.assertEqual(self.product.absolute_url, expected_absolute_url)

    def tearDown(self) -> None:
        super().tearDown()
        self.product_favourite.delete()
        self.product.delete()
        self.user.delete()
        self.category.delete()
        self.vat.delete()
