from django.db import IntegrityError
from django.test import TestCase

from product.enum.category import CategoryImageTypeEnum
from product.factories.category import ProductCategoryFactory
from product.factories.category_image import ProductCategoryImageFactory
from product.models.category_image import ProductCategoryImage


class ProductCategoryImageModelTestCase(TestCase):
    def setUp(self):
        self.category = ProductCategoryFactory()

    def test_create_category_image(self):
        image = ProductCategoryImageFactory(
            category=self.category, image_type=CategoryImageTypeEnum.MAIN
        )

        self.assertEqual(image.category, self.category)
        self.assertEqual(image.image_type, CategoryImageTypeEnum.MAIN)
        self.assertTrue(image.active)
        self.assertIsNotNone(image.image)

    def test_unique_image_type_per_category(self):
        ProductCategoryImageFactory(
            category=self.category, image_type=CategoryImageTypeEnum.MAIN
        )

        with self.assertRaises(IntegrityError):
            ProductCategoryImageFactory(
                category=self.category, image_type=CategoryImageTypeEnum.MAIN
            )

    def test_multiple_image_types_same_category(self):
        main_image = ProductCategoryImageFactory(
            category=self.category, image_type=CategoryImageTypeEnum.MAIN
        )
        banner_image = ProductCategoryImageFactory(
            category=self.category, image_type=CategoryImageTypeEnum.BANNER
        )

        self.assertEqual(main_image.category, self.category)
        self.assertEqual(banner_image.category, self.category)
        self.assertNotEqual(main_image.image_type, banner_image.image_type)

    def test_str_representation(self):
        image = ProductCategoryImageFactory(
            category=self.category, image_type=CategoryImageTypeEnum.BANNER
        )

        category_name = self.category.safe_translation_getter(
            "name", any_language=True
        )
        expected_str = f"{category_name} - Banner Image"
        self.assertEqual(str(image), expected_str)

    def test_class_methods(self):
        main_image = ProductCategoryImageFactory(
            category=self.category, image_type=CategoryImageTypeEnum.MAIN
        )
        banner_image = ProductCategoryImageFactory(
            category=self.category, image_type=CategoryImageTypeEnum.BANNER
        )

        retrieved_main = ProductCategoryImage.get_main_image(self.category)
        retrieved_banner = ProductCategoryImage.get_banner_image(self.category)
        retrieved_by_type = ProductCategoryImage.get_image_by_type(
            self.category, CategoryImageTypeEnum.MAIN
        )

        self.assertEqual(retrieved_main, main_image)
        self.assertEqual(retrieved_banner, banner_image)
        self.assertEqual(retrieved_by_type, main_image)

    def test_image_properties(self):
        image = ProductCategoryImageFactory(
            category=self.category, image_type=CategoryImageTypeEnum.ICON
        )

        self.assertIsNotNone(image.image_path)
        self.assertIsNotNone(image.image_url)
        self.assertIn("category", image.image_path)

    def test_manager_methods(self):
        active_image = ProductCategoryImageFactory(
            category=self.category,
            active=True,
            image_type=CategoryImageTypeEnum.GALLERY,
        )
        inactive_image = ProductCategoryImageFactory(
            category=self.category,
            active=False,
            image_type=CategoryImageTypeEnum.THUMBNAIL,
        )

        active_images = ProductCategoryImage.objects.active()
        category_images = ProductCategoryImage.objects.by_category(
            self.category
        )

        self.assertIn(active_image, active_images)
        self.assertNotIn(inactive_image, active_images)
        self.assertIn(active_image, category_images)
        self.assertIn(inactive_image, category_images)
