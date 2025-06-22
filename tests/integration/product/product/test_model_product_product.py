import os
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Avg
from django.test import TestCase
from django.utils.html import format_html
from djmoney.money import Money

from product.enum.review import ReviewStatus
from product.factories.category import ProductCategoryFactory
from product.factories.favourite import ProductFavouriteFactory
from product.factories.image import ProductImageFactory
from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from product.models.product import Product
from product.models.review import ProductReview
from user.factories.account import UserAccountFactory
from vat.factories import VatFactory

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class ProductModelTestCase(TestCase):
    images: list = []
    reviews: list = []

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
            status=ReviewStatus.TRUE,
        )
        self.reviews.append(product_review_status_true)

        product_review_status_false = ProductReviewFactory(
            product=self.product,
            user=user_2,
            rate=5,
            status=ReviewStatus.FALSE,
        )
        self.reviews.append(product_review_status_false)

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
        self.assertEqual(
            self.product.price_save_percent, expected_price_save_percent
        )

    def test_fields(self):
        self.assertEqual(self.product.product_code, "P123456")
        self.assertEqual(self.product.slug, "sample-product")
        self.assertEqual(
            self.product.price, Money("100.00", settings.DEFAULT_CURRENCY)
        )
        self.assertEqual(self.product.active, True)
        self.assertEqual(self.product.stock, 10)
        self.assertEqual(self.product.discount_percent, Decimal("50.0"))
        self.assertEqual(self.product.vat, self.vat)
        self.assertEqual(self.product.view_count, 10)
        self.assertEqual(self.product.weight, Decimal("5.00"))

    def test_str_representation(self):
        self.assertEqual(
            str(self.product), self.product.safe_translation_getter("name")
        )

    def test_likes_count(self):
        self.assertEqual(self.product.likes_count, 1)

    def test_review_average(self):
        self.assertEqual(self.product.review_average, 5)

    def test_review_count(self):
        self.assertEqual(self.product.review_count, 2)

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
        expected_filename = (
            f"media/uploads/products/{os.path.basename(main_image.image.name)}"
        )
        self.assertEqual(self.product.main_image_path, expected_filename)

    def test_colored_stock_property(self):
        self.product.stock = 5
        self.assertEqual(
            self.product.colored_stock,
            format_html(
                '<span style="color: #1bff00;">{}</span>', self.product.stock
            ),
        )

        self.product.stock = 0
        self.assertEqual(
            self.product.colored_stock,
            format_html(
                '<span style="color: #ff0000;">{}</span>', self.product.stock
            ),
        )


class ProductQuerySetTestCase(TestCase):
    def setUp(self):
        self.vat = VatFactory(value=Decimal("10.0"))

        self.product1 = ProductFactory(
            price=Decimal("100.00"),
            discount_percent=Decimal("20.0"),
            vat=self.vat,
            stock=10,
            active=True,
        )

        ProductReviewFactory(
            product=self.product1, rate=5, status=ReviewStatus.TRUE
        )
        ProductReviewFactory(
            product=self.product1, rate=3, status=ReviewStatus.TRUE
        )
        ProductReviewFactory(
            product=self.product1, rate=4, status=ReviewStatus.FALSE
        )

        user1 = UserAccountFactory(num_addresses=0)
        user2 = UserAccountFactory(num_addresses=0)
        ProductFavouriteFactory(product=self.product1, user=user1)
        ProductFavouriteFactory(product=self.product1, user=user2)

        self.product2 = ProductFactory(
            price=Decimal("0.00"),
            discount_percent=Decimal("0.0"),
            vat=None,
            stock=0,
            active=False,
        )

        self.product3 = ProductFactory(
            price=Decimal("50.00"),
            discount_percent=Decimal("10.0"),
            vat=None,
            stock=5,
            active=True,
        )

    def test_queryset_with_discount_value(self):
        queryset = Product.objects.with_discount_value()
        product1 = queryset.get(id=self.product1.id)
        product2 = queryset.get(id=self.product2.id)

        expected_discount_value1 = (
            self.product1.price.amount * self.product1.discount_percent
        ) / 100
        expected_discount_value2 = (
            self.product2.price.amount * self.product2.discount_percent
        ) / 100

        self.assertEqual(
            product1.discount_value_amount, expected_discount_value1
        )
        self.assertEqual(
            product2.discount_value_amount, expected_discount_value2
        )

    def test_queryset_with_vat_value(self):
        queryset = Product.objects.with_vat_value()
        product1 = queryset.get(id=self.product1.id)
        product2 = queryset.get(id=self.product2.id)
        product3 = queryset.get(id=self.product3.id)

        expected_vat_value1 = (
            self.product1.price.amount * self.vat.value
        ) / 100
        expected_vat_value2 = Decimal("0.00")
        expected_vat_value3 = Decimal("0.00")

        self.assertEqual(product1.vat_value_amount, expected_vat_value1)
        self.assertEqual(product2.vat_value_amount, expected_vat_value2)
        self.assertEqual(product3.vat_value_amount, expected_vat_value3)

    def test_queryset_with_final_price(self):
        queryset = Product.objects.with_final_price()
        product1 = queryset.get(id=self.product1.id)

        expected_discount_value = (
            self.product1.price.amount * self.product1.discount_percent
        ) / 100
        expected_vat_value = (self.product1.price.amount * self.vat.value) / 100
        expected_final_price = (
            self.product1.price.amount
            + expected_vat_value
            - expected_discount_value
        )

        self.assertEqual(product1.final_price_amount, expected_final_price)

    def test_queryset_with_price_save_percent(self):
        queryset = Product.objects.with_price_save_percent()
        product1 = queryset.get(id=self.product1.id)
        product2 = queryset.get(id=self.product2.id)

        expected_discount_value1 = (
            self.product1.price.amount * self.product1.discount_percent
        ) / 100
        expected_price_save_percent1 = (
            expected_discount_value1 / self.product1.price.amount
        ) * 100

        self.assertEqual(
            product1.price_save_percent_field, expected_price_save_percent1
        )
        self.assertEqual(product2.price_save_percent_field, Decimal("0"))

    def test_queryset_with_likes_count(self):
        queryset = Product.objects.with_likes_count()
        product1 = queryset.get(id=self.product1.id)
        product2 = queryset.get(id=self.product2.id)

        self.assertEqual(product1.likes_count_field, 2)
        self.assertEqual(product2.likes_count_field, 0)

    def test_queryset_with_review_average(self):
        queryset = Product.objects.with_review_average()
        product1 = queryset.get(id=self.product1.id)
        product2 = queryset.get(id=self.product2.id)

        actual_avg = ProductReview.objects.filter(
            product=self.product1
        ).aggregate(avg=Avg("rate"))["avg"]

        self.assertEqual(product1.review_average_field, actual_avg)
        self.assertEqual(product2.review_average_field, 0)

    def test_queryset_with_all_annotations(self):
        queryset = Product.objects.with_all_annotations()
        product1 = queryset.get(id=self.product1.id)

        self.assertTrue(hasattr(product1, "discount_value_amount"))
        self.assertTrue(hasattr(product1, "vat_value_amount"))
        self.assertTrue(hasattr(product1, "final_price_amount"))
        self.assertTrue(hasattr(product1, "price_save_percent_field"))
        self.assertTrue(hasattr(product1, "likes_count_field"))
        self.assertTrue(hasattr(product1, "review_average_field"))

    def test_annotations_match_property_values(self):
        queryset = Product.objects.with_all_annotations()
        product1 = queryset.get(id=self.product1.id)

        product_instance = Product.objects.get(id=self.product1.id)

        self.assertEqual(
            product1.discount_value_amount,
            product_instance.discount_value.amount,
        )
        self.assertEqual(
            product1.final_price_amount, product_instance.final_price.amount
        )
        self.assertEqual(
            product1.price_save_percent_field,
            product_instance.price_save_percent,
        )
        self.assertEqual(
            product1.likes_count_field, product_instance.likes_count
        )
        self.assertEqual(
            product1.review_average_field, product_instance.review_average
        )

    def test_ordering_with_annotations(self):
        ProductFactory(
            price=Decimal("200.00"), discount_percent=Decimal("30.0")
        )
        ProductFactory(
            price=Decimal("300.00"), discount_percent=Decimal("10.0")
        )

        products_by_discount = list(
            Product.objects.with_discount_value().order_by(
                "-discount_value_amount"
            )
        )
        self.assertTrue(
            products_by_discount[0].discount_value_amount
            >= products_by_discount[1].discount_value_amount
        )

        products_by_price = list(
            Product.objects.with_final_price().order_by("-final_price_amount")
        )
        self.assertTrue(
            products_by_price[0].final_price_amount
            >= products_by_price[1].final_price_amount
        )
