import os
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.html import format_html
from djmoney.money import Money

from product.enum.review import ReviewStatusEnum
from product.factories.category import ProductCategoryFactory
from product.factories.favourite import ProductFavouriteFactory
from product.factories.image import ProductImageFactory
from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from product.models.category import ProductCategory
from product.models.favourite import ProductFavourite
from product.models.image import ProductImage
from product.models.product import Product
from product.models.review import ProductReview
from user.factories.account import UserAccountFactory
from vat.factories import VatFactory
from vat.models import Vat

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class ProductModelTestCase(TestCase):
    product: Product = None
    user: User = None
    category: ProductCategory = None
    vat: Vat = None
    images: list[ProductImage] = []
    reviews: list[ProductReview] = []
    favourite: ProductFavourite = None

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.category = ProductCategoryFactory()
        self.vat = VatFactory()
        self.product = ProductFactory(
            product_code="P123456",
            category=self.category,
            vat=self.vat,
            slug="sample-product",
            price=Decimal("100.00"),
            active=True,
            stock=10,
            discount_percent=Decimal("50.0"),
            view_count=10,
            weight=Decimal("5.00"),
        )

        main_product_image = ProductImageFactory(
            product=self.product,
            is_main=True,
        )

        self.images.append(main_product_image)

        non_main_product_image = ProductImageFactory(
            product=self.product,
            is_main=False,
        )

        self.images.append(non_main_product_image)

        self.favourite = ProductFavouriteFactory(
            product=self.product,
            user=self.user,
        )

        user_2 = UserAccountFactory(num_addresses=0)

        product_review_status_true = ProductReviewFactory(
            product=self.product,
            user=self.user,
            rate=5,
            status=ReviewStatusEnum.TRUE,
        )
        self.reviews.append(product_review_status_true)

        product_review_status_false = ProductReviewFactory(
            product=self.product,
            user=user_2,
            rate=5,
            status=ReviewStatusEnum.FALSE,
        )
        self.reviews.append(product_review_status_false)

    def test_save_method(self):
        self.product.price = Decimal("100.00")
        self.product.discount_percent = Decimal("50.0")
        self.product.vat = self.vat

        self.product.save()
        self.product.refresh_from_db()

        expected_discount_value = (self.product.price * self.product.discount_percent) / 100
        expected_vat_value = (self.product.price * self.vat.value) / 100
        expected_final_price = self.product.price + expected_vat_value - expected_discount_value
        expected_price_save_percent = (expected_discount_value / self.product.price) * 100

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

    def test_str_representation(self):
        self.assertEqual(str(self.product), self.product.safe_translation_getter("name"))

    def test_likes_count(self):
        self.assertEqual(self.product.likes_count, 1)

    def test_review_average(self):
        self.assertEqual(self.product.review_average, 5)

    def test_approved_review_average(self):
        self.assertEqual(self.product.approved_review_average, 5)

    def test_review_count(self):
        self.assertEqual(self.product.review_count, 2)

    def test_approved_review_count(self):
        self.assertEqual(self.product.approved_review_count, 1)

    def test_vat_percent(self):
        self.assertEqual(self.product.vat_percent, self.vat.value)

    def test_vat_value(self):
        expected_vat_value = (self.product.price.amount * self.vat.value) / 100
        self.assertEqual(self.product.vat_value.amount, expected_vat_value)

    def test_product_with_tags(self):
        product = ProductFactory(num_tags=3)
        self.assertEqual(product.tags.count(), 3)

    def test_main_image_path(self):
        main_image = self.product.images.filter(is_main=True).first()
        expected_filename = f"media/uploads/products/{os.path.basename(main_image.image.name)}"
        self.assertEqual(self.product.main_image_path, expected_filename)

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
        expected_absolute_url = f"/products/{self.product.id}/{self.product.slug}"
        self.assertEqual(self.product.absolute_url, expected_absolute_url)

    def tearDown(self) -> None:
        Product.objects.all().delete()
        User.objects.all().delete()
        ProductCategory.objects.all().delete()
        Vat.objects.all().delete()
        ProductImage.objects.all().delete()
        ProductReview.objects.all().delete()
        ProductFavourite.objects.all().delete()
        super().tearDown()
