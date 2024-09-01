import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import TestCase

from product.factories.category import ProductCategoryFactory
from product.factories.product import ProductFactory
from product.models.category import ProductCategory
from user.factories.account import UserAccountFactory
from vat.factories import VatFactory
from vat.models import Vat

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class CategoryModelTestCase(TestCase):
    category: ProductCategory = None
    sub_category: ProductCategory = None
    user: User = None
    vat: Vat = None

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.vat = VatFactory()
        self.category = ProductCategoryFactory()
        self.sub_category = ProductCategoryFactory(parent=self.category)

    def test_fields(self):
        self.assertTrue(default_storage.exists(self.category.menu_image_one.path))
        self.assertTrue(default_storage.exists(self.category.menu_image_two.path))
        self.assertTrue(default_storage.exists(self.category.menu_main_banner.path))

    def test_unicode_representation(self):
        self.assertEqual(
            self.category.__unicode__(),
            self.category.safe_translation_getter("name"),
        )

    def test_str_representation_no_parent(self):
        self.assertEqual(str(self.category), self.category.safe_translation_getter("name"))

    def test_get_ordering_queryset_with_parent(self):
        self.assertEqual(ProductCategory.objects.count(), 2)
        self.assertIn(self.sub_category, ProductCategory.objects.all())
        self.assertIn(self.category, ProductCategory.objects.all())

        parent_queryset = self.category.get_ordering_queryset()

        self.assertIn(self.sub_category, parent_queryset)

        self.assertIn(self.category, parent_queryset)

    def test_get_ordering_queryset_without_parent(self):
        no_parent_category = ProductCategoryFactory(
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
        ProductFactory(category=self.category, vat=self.vat, num_images=0, num_reviews=0)

        count = self.category.recursive_product_count
        self.assertEqual(count, 1)

    def test_recursive_product_count_multiple_products(self):
        ProductFactory(category=self.category, num_images=0, num_reviews=0)
        ProductFactory(category=self.sub_category, num_images=0, num_reviews=0)

        count = self.category.recursive_product_count
        self.assertEqual(count, 2)

    def test_absolute_url_no_parent(self):
        url = self.category.absolute_url
        expected_url = f"/product/category/{self.category.id}/{self.category.slug}"
        self.assertEqual(url, expected_url)

    def test_absolute_url_with_parent(self):
        url = self.sub_category.absolute_url
        expected_url = f"/product/category/{self.sub_category.id}/" f"{self.category.slug}/{self.sub_category.slug}"
        self.assertEqual(url, expected_url)

    def category_menu_image_one_path(self):
        expected_path = f"media/uploads/categories/{os.path.basename(self.category.menu_image_one.name)}"
        self.assertEqual(self.category.category_menu_image_one_path, expected_path)

    def category_menu_image_two_path(self):
        expected_path = f"media/uploads/categories/{os.path.basename(self.category.menu_image_two.name)}"
        self.assertEqual(self.category.category_menu_image_two_path, expected_path)

    def category_menu_main_banner_path(self):
        expected_path = f"media/uploads/categories/{os.path.basename(self.category.menu_main_banner.name)}"
        self.assertEqual(self.category.category_menu_main_banner_path, expected_path)

    def tearDown(self) -> None:
        ProductCategory.objects.all().delete()
        User.objects.all().delete()
        Vat.objects.all().delete()
        super().tearDown()
