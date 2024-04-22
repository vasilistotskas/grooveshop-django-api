import os
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import override_settings
from django.test import TestCase

from helpers.seed import get_or_create_default_image
from product.models.category import ProductCategory
from product.models.product import Product
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
class CategoryModelTestCase(TestCase):
    category = None
    sub_category = None
    user = None
    vat = None
    default_image = None

    def setUp(self):
        self.user = User.objects.create_user(
            email="test@test.com", password="test12345@!"
        )
        self.vat = Vat.objects.create(
            value=Decimal("24.0"),
        )

        self.default_image = get_or_create_default_image(
            "uploads/categories/no_photo.jpg"
        )

        self.category = ProductCategory.objects.create(
            slug="sample-category",
            menu_image_one=self.default_image,
            menu_image_two=self.default_image,
            menu_main_banner=self.default_image,
        )

        for language in languages:
            self.category.set_current_language(language)
            self.category.name = f"Sample Category {language}"
            self.category.description = f"Sample Category Description {language}"
            self.category.save()
        self.category.set_current_language(default_language)

        self.sub_category = ProductCategory.objects.create(
            slug="sample-sub-category",
            parent=self.category,
            menu_image_one=self.default_image,
            menu_image_two=self.default_image,
            menu_main_banner=self.default_image,
        )

        for language in languages:
            self.sub_category.set_current_language(language)
            self.sub_category.name = f"Sample Sub Category {language}"
            self.sub_category.description = (
                f"Sample Sub Category Description {language}"
            )
            self.sub_category.save()
        self.sub_category.set_current_language(default_language)

    def test_fields(self):
        self.assertEqual(self.category.slug, "sample-category")
        self.assertTrue(default_storage.exists(self.category.menu_image_one.path))
        self.assertTrue(default_storage.exists(self.category.menu_image_two.path))
        self.assertTrue(default_storage.exists(self.category.menu_main_banner.path))

    def test_unicode_representation(self):
        self.assertEqual(
            self.category.__unicode__(),
            self.category.safe_translation_getter("name"),
        )

    def test_translations(self):
        for language in languages:
            self.category.set_current_language(language)
            self.assertEqual(self.category.name, f"Sample Category {language}")
            self.assertEqual(
                self.category.description, f"Sample Category Description {language}"
            )

    def test_str_representation_no_parent(self):
        self.assertEqual(
            str(self.category), self.category.safe_translation_getter("name")
        )

    def test_str_representation_with_parent(self):
        category_name = self.category.safe_translation_getter("name")
        expected_str = f"{category_name} / Sample Sub Category" f" {default_language}"
        self.assertEqual(str(self.sub_category), expected_str)

    def test_str_representation_with_grandparent(self):
        grandparent = ProductCategory.objects.create(
            slug="grandparent-category",
        )
        for language in languages:
            grandparent.set_current_language(language)
            grandparent.name = f"Grandparent Category {language}"
            grandparent.description = f"Grandparent Category Description {language}"
            grandparent.save()
        grandparent.set_current_language(default_language)

        self.sub_category.parent = grandparent
        self.sub_category.save()

        expected_str = (
            f"Grandparent Category {default_language} / Sample Sub Category"
            f" {default_language}"
        )
        self.assertEqual(str(self.sub_category), expected_str)

    def test_get_ordering_queryset_with_parent(self):
        self.assertEqual(ProductCategory.objects.count(), 2)
        self.assertIn(self.sub_category, ProductCategory.objects.all())
        self.assertIn(self.category, ProductCategory.objects.all())

        parent_queryset = self.category.get_ordering_queryset()

        self.assertIn(self.sub_category, parent_queryset)

        self.assertIn(self.category, parent_queryset)

    def test_get_ordering_queryset_without_parent(self):
        no_parent_category = ProductCategory.objects.create(
            slug="no-parent-category",
        )

        parent_queryset = no_parent_category.get_ordering_queryset()

        self.assertIn(no_parent_category, parent_queryset)

        self.assertIn(self.sub_category, parent_queryset)
        self.assertIn(self.category, parent_queryset)

        for descendant in self.category.get_descendants(include_self=True):
            self.assertIn(descendant, parent_queryset)

    def test_recursive_product_count_no_products(self):
        count = self.category.recursive_product_count
        self.assertEqual(count, 0)

    def test_recursive_product_count_one_product(self):
        Product.objects.create(
            product_code="P123",
            category=self.category,
            slug="product-1",
            price=Decimal("100.0"),
            active=True,
            stock=10,
            discount_percent=Decimal("0.0"),
            vat=Vat.objects.create(value=Decimal("18.0")),
            view_count=10,
            weight=Decimal("1.0"),
        )

        count = self.category.recursive_product_count
        self.assertEqual(count, 1)

    def test_recursive_product_count_multiple_products(self):
        Product.objects.create(
            product_code="P123",
            category=self.category,
            slug="product-1",
            price=Decimal("100.0"),
            active=True,
            stock=10,
            discount_percent=Decimal("0.0"),
            vat=Vat.objects.create(value=Decimal("18.0")),
            view_count=10,
            weight=Decimal("1.0"),
        )
        Product.objects.create(
            product_code="P124",
            category=self.sub_category,
            slug="product-2",
            price=Decimal("150.0"),
            active=True,
            stock=8,
            discount_percent=Decimal("0.0"),
            vat=Vat.objects.create(value=Decimal("18.0")),
            view_count=8,
            weight=Decimal("1.5"),
        )

        count = self.category.recursive_product_count
        self.assertEqual(count, 2)

    def test_absolute_url_no_parent(self):
        url = self.category.absolute_url
        expected_url = f"/product/category/{self.category.id}/{self.category.slug}"
        self.assertEqual(url, expected_url)

    def test_absolute_url_with_parent(self):
        url = self.sub_category.absolute_url
        expected_url = (
            f"/product/category/{self.sub_category.id}/"
            f"{self.category.slug}/{self.sub_category.slug}"
        )
        self.assertEqual(url, expected_url)

    def test_category_menu_image_one_absolute_url(self):
        expected_url = settings.APP_BASE_URL + self.category.menu_image_one.url
        self.assertEqual(
            self.category.category_menu_image_one_absolute_url, expected_url
        )

    def category_menu_image_one_filename(self):
        expected_filename = os.path.basename(self.category.menu_image_one.name)
        self.assertEqual(
            self.category.category_menu_image_one_filename, expected_filename
        )

    def test_category_menu_image_two_absolute_url(self):
        expected_url = settings.APP_BASE_URL + self.category.menu_image_two.url
        self.assertEqual(
            self.category.category_menu_image_two_absolute_url, expected_url
        )

    def category_menu_image_two_filename(self):
        expected_filename = os.path.basename(self.category.menu_image_two.name)
        self.assertEqual(
            self.category.category_menu_image_two_filename, expected_filename
        )

    def test_category_menu_main_banner_absolute_url(self):
        expected_url = settings.APP_BASE_URL + self.category.menu_main_banner.url
        self.assertEqual(
            self.category.category_menu_main_banner_absolute_url, expected_url
        )

    def category_menu_main_banner_filename(self):
        expected_filename = os.path.basename(self.category.menu_main_banner.name)
        self.assertEqual(
            self.category.category_menu_main_banner_filename, expected_filename
        )

    def tearDown(self) -> None:
        super().tearDown()
        self.user.delete()
        self.vat.delete()
        self.sub_category.delete()
        self.category.delete()
