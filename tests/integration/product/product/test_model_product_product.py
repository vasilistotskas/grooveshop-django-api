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
from product.enum.review import ReviewStatusEnum
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
        self.user = User.objects.create_user(
            email="test@test.com", password="test12345@!"
        )
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

        self.vat = Vat.objects.create(
            value=Decimal("24.0"),
        )

        self.product = Product.objects.create(
            product_code="P123456",
            category=self.category,
            slug="sample-product",
            price=Decimal("100.00"),
            active=True,
            stock=10,
            discount_percent=Decimal("50.0"),
            vat=self.vat,
            view_count=10,
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

        self.product_favourite = ProductFavourite.objects.create(
            product=self.product,
            user=self.user,
        )

        user_2 = User.objects.create_user(
            email="test2@test.com", password="test12345@!"
        )

        product_review_status_true = ProductReview.objects.create(
            product=self.product,
            user=self.user,
            rate=5,
            status=ReviewStatusEnum.TRUE,
            comment="Sample Product Review Comment",
        )
        self.product_reviews.append(product_review_status_true)

        product_review_status_false = ProductReview.objects.create(
            product=self.product,
            user=user_2,
            rate=5,
            status=ReviewStatusEnum.FALSE,
            comment="Sample Product Review Comment",
        )
        self.product_reviews.append(product_review_status_false)

    def test_save_method(self):
        self.product.price = Decimal("100.00")
        self.product.discount_percent = Decimal("50.0")
        self.product.vat = self.vat

        self.product.save()
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
        self.assertEqual(self.product.product_code, "P123456")
        self.assertEqual(self.product.slug, "sample-product")
        self.assertEqual(self.product.price, Money("100.00", settings.DEFAULT_CURRENCY))
        self.assertEqual(self.product.active, True)
        self.assertEqual(self.product.stock, 10)
        self.assertEqual(self.product.discount_percent, Decimal("50.0"))
        self.assertEqual(self.product.vat, self.vat)
        self.assertEqual(self.product.view_count, 10)
        self.assertEqual(self.product.weight, Decimal("5.00"))

    def test_unicode_representation(self):
        self.assertEqual(
            self.product.__unicode__(),
            self.product.safe_translation_getter("name"),
        )

    def test_translations(self):
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
        self.assertEqual(
            str(self.product), self.product.safe_translation_getter("name")
        )

    def test_likes_count(self):
        self.assertEqual(self.product.likes_count, 1)

    def test_review_average(self):
        self.assertEqual(self.product.review_average, 5)

    def test_review_count(self):
        self.assertEqual(self.product.review_count, 1)

    def test_vat_percent(self):
        self.assertEqual(self.product.vat_percent, self.vat.value)

    def test_vat_value(self):
        expected_vat_value = (self.product.price.amount * self.vat.value) / 100
        self.assertEqual(self.product.vat_value.amount, expected_vat_value)

    def test_main_image_absolute_url(self):
        main_image = self.product.product_images.filter(is_main=True).first()
        expected_url = settings.APP_BASE_URL + main_image.image.url
        self.assertEqual(self.product.main_image_absolute_url, expected_url)

    def test_main_image_filename(self):
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
