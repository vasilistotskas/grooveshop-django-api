from datetime import timedelta

import pytest
from django.apps import apps
from django.core.files.base import ContentFile
from django.utils import timezone

from product.factories import ProductFactory, ProductImageFactory
from product.models.image import ProductImage


@pytest.fixture
def product():
    return ProductFactory()


@pytest.fixture
def product_image(product):
    return ProductImageFactory(product=product, is_main=True)


@pytest.fixture
def secondary_image(product):
    return ProductImageFactory(product=product, is_main=False)


@pytest.mark.django_db
class TestEnhancedImageQuerySet:
    def test_main_images(self, product_image, secondary_image):
        main_images = ProductImage.objects.main_images()

        assert product_image in main_images
        assert secondary_image not in main_images

    def test_secondary_images(self, product_image, secondary_image):
        secondary_images = ProductImage.objects.secondary_images()

        assert secondary_image in secondary_images
        assert product_image not in secondary_images

    def test_for_product_with_object(self, product, product_image):
        other_product = ProductFactory()
        other_image = ProductImageFactory(product=other_product)

        product_images = ProductImage.objects.for_product(product)

        assert product_image in product_images
        assert other_image not in product_images

    def test_for_product_with_id(self, product, product_image):
        other_product = ProductFactory()
        other_image = ProductImageFactory(product=other_product)

        product_images = ProductImage.objects.for_product(product.pk)

        assert product_image in product_images
        assert other_image not in product_images

    def test_for_products(self, product, product_image):
        product2 = ProductFactory()
        image2 = ProductImageFactory(product=product2)
        product3 = ProductFactory()
        image3 = ProductImageFactory(product=product3)

        product_ids = [product.pk, product2.pk]
        images = ProductImage.objects.for_products(product_ids)

        assert product_image in images
        assert image2 in images
        assert image3 not in images

    def test_with_titles(self):
        image_with_title = ProductImageFactory(product=ProductFactory())

        ProductImageTranslation = apps.get_model(
            "product", "ProductImageTranslation"
        )

        image_without_title = ProductImageFactory(product=ProductFactory())
        image_without_title.translations.all().delete()
        ProductImageTranslation.objects.create(
            master=image_without_title, language_code="el", title=None
        )

        images_with_titles = ProductImage.objects.with_titles()

        assert image_with_title in images_with_titles
        assert image_without_title not in images_with_titles

    def test_without_titles(self):
        image_with_title = ProductImageFactory(product=ProductFactory())

        ProductImageTranslation = apps.get_model(
            "product", "ProductImageTranslation"
        )

        image_without_title = ProductImageFactory(product=ProductFactory())
        image_without_title.translations.all().delete()
        ProductImageTranslation.objects.create(
            master=image_without_title, language_code="el", title=None
        )

        images_without_titles = ProductImage.objects.without_titles()

        assert image_without_title in images_without_titles
        assert image_with_title not in images_without_titles

    def test_recent_default_days(self, product_image):
        recent_image = ProductImageFactory()
        old_image = ProductImageFactory()

        old_time = timezone.now() - timedelta(days=10)
        ProductImage.objects.filter(id=old_image.id).update(created_at=old_time)

        recent_images = ProductImage.objects.recent()

        assert product_image in recent_images
        assert recent_image in recent_images
        assert old_image not in recent_images

    def test_recent_custom_days(self, product_image):
        recent_image = ProductImageFactory()
        old_image = ProductImageFactory()

        old_time = timezone.now() - timedelta(days=5)
        ProductImage.objects.filter(id=old_image.id).update(created_at=old_time)

        recent_images = ProductImage.objects.recent(days=3)

        assert product_image in recent_images
        assert recent_image in recent_images
        assert old_image not in recent_images

    def test_ordered_by_position(self, product):
        image1 = ProductImage.objects.create(
            product=product,
            image=ContentFile(b"fake image 1", name="image1.jpg"),
        )
        image2 = ProductImage.objects.create(
            product=product,
            image=ContentFile(b"fake image 2", name="image2.jpg"),
        )
        image3 = ProductImage.objects.create(
            product=product,
            image=ContentFile(b"fake image 3", name="image3.jpg"),
        )

        image1.sort_order = 3
        image1.save()
        image2.sort_order = 1
        image2.save()
        image3.sort_order = 2
        image3.save()

        ordered_images = list(ProductImage.objects.ordered_by_position())

        assert ordered_images.index(image2) < ordered_images.index(image3)
        assert ordered_images.index(image3) < ordered_images.index(image1)

    def test_optimized_for_list(self, product_image):
        optimized_images = ProductImage.objects.optimized_for_list()
        image = optimized_images.get(id=product_image.id)

        assert hasattr(image, "product")

    def test_with_product_data(self, product_image):
        images_with_data = ProductImage.objects.with_product_data()
        image = images_with_data.get(id=product_image.id)

        assert hasattr(image, "product")

    def test_get_products_needing_images_default_threshold(self):
        product1 = ProductFactory()
        ProductImageFactory.create_batch(3, product=product1)

        product2 = ProductFactory()
        ProductImageFactory.create_batch(6, product=product2)

        products_needing_images = (
            ProductImage.objects.get_products_needing_images()
        )

        assert product1 in products_needing_images
        assert product2 not in products_needing_images

    def test_get_products_needing_images_custom_threshold(self):
        product1 = ProductFactory()
        ProductImageFactory.create_batch(2, product=product1)

        product2 = ProductFactory()
        ProductImageFactory.create_batch(4, product=product2)

        products_needing_images = (
            ProductImage.objects.get_products_needing_images(max_images=3)
        )

        assert product1 in products_needing_images
        assert product2 not in products_needing_images

    def test_get_products_without_main_image(self):
        product_with_main = ProductFactory()
        ProductImageFactory(product=product_with_main, is_main=True)

        product_without_main = ProductFactory()
        ProductImageFactory(product=product_without_main, is_main=False)

        product_no_images = ProductFactory()

        products_without_main = (
            ProductImage.objects.get_products_without_main_image()
        )

        assert product_with_main not in products_without_main
        assert product_without_main in products_without_main
        assert product_no_images in products_without_main

    def test_bulk_update_sort_order(self, product):
        image1 = ProductImageFactory(product=product, sort_order=1)
        image2 = ProductImageFactory(product=product, sort_order=2)
        image3 = ProductImageFactory(product=product, sort_order=3)

        image_orders = {
            image1.id: 10,
            image2.id: 20,
            image3.id: 30,
        }

        updated_count = ProductImage.objects.bulk_update_sort_order(
            image_orders
        )

        assert updated_count == 3

        image1.refresh_from_db()
        image2.refresh_from_db()
        image3.refresh_from_db()

        assert image1.sort_order == 10
        assert image2.sort_order == 20
        assert image3.sort_order == 30

    def test_bulk_update_sort_order_empty_dict(self):
        updated_count = ProductImage.objects.bulk_update_sort_order({})
        assert updated_count == 0

    def test_bulk_update_sort_order_nonexistent_ids(self):
        image_orders = {
            99999: 10,
            99998: 20,
        }

        updated_count = ProductImage.objects.bulk_update_sort_order(
            image_orders
        )
        assert updated_count == 0


@pytest.mark.django_db
class TestEnhancedImageManager:
    def test_main_image_returns_first_main_image(self, product):
        main_image1 = ProductImageFactory(product=product, is_main=True)
        ProductImageFactory(product=product, is_main=False)

        result = ProductImage.objects.main_image(product)

        assert result == main_image1

    def test_main_image_returns_none_if_no_main_image(self, product):
        ProductImageFactory(product=product, is_main=False)

        result = ProductImage.objects.main_image(product)

        assert result is None

    def test_manager_delegates_to_queryset_main_images(
        self, product_image, secondary_image
    ):
        main_images = ProductImage.objects.main_images()

        assert product_image in main_images
        assert secondary_image not in main_images

    def test_manager_delegates_to_queryset_secondary_images(
        self, product_image, secondary_image
    ):
        secondary_images = ProductImage.objects.secondary_images()

        assert secondary_image in secondary_images
        assert product_image not in secondary_images

    def test_manager_delegates_to_queryset_for_product(
        self, product, product_image
    ):
        other_product = ProductFactory()
        other_image = ProductImageFactory(product=other_product)

        product_images = ProductImage.objects.for_product(product)

        assert product_image in product_images
        assert other_image not in product_images

    def test_manager_delegates_to_queryset_for_products(
        self, product, product_image
    ):
        product2 = ProductFactory()
        image2 = ProductImageFactory(product=product2)
        product3 = ProductFactory()
        image3 = ProductImageFactory(product=product3)

        product_ids = [product.pk, product2.pk]
        images = ProductImage.objects.for_products(product_ids)

        assert product_image in images
        assert image2 in images
        assert image3 not in images

    def test_manager_delegates_to_queryset_with_titles(self):
        image_with_title = ProductImageFactory(product=ProductFactory())

        ProductImageTranslation = apps.get_model(
            "product", "ProductImageTranslation"
        )

        image_without_title = ProductImageFactory(product=ProductFactory())
        image_without_title.translations.all().delete()
        ProductImageTranslation.objects.create(
            master=image_without_title, language_code="el", title=None
        )

        images_with_titles = ProductImage.objects.with_titles()

        assert image_with_title in images_with_titles
        assert image_without_title not in images_with_titles

    def test_manager_delegates_to_queryset_without_titles(self):
        image_with_title = ProductImageFactory(product=ProductFactory())

        ProductImageTranslation = apps.get_model(
            "product", "ProductImageTranslation"
        )

        image_without_title = ProductImageFactory(product=ProductFactory())
        image_without_title.translations.all().delete()
        ProductImageTranslation.objects.create(
            master=image_without_title, language_code="el", title=None
        )

        images_without_titles = ProductImage.objects.without_titles()

        assert image_without_title in images_without_titles
        assert image_with_title not in images_without_titles

    def test_manager_delegates_to_queryset_recent(self, product_image):
        recent_image = ProductImageFactory()
        old_image = ProductImageFactory()

        old_time = timezone.now() - timedelta(days=10)
        ProductImage.objects.filter(id=old_image.id).update(created_at=old_time)

        recent_images = ProductImage.objects.recent()

        assert product_image in recent_images
        assert recent_image in recent_images
        assert old_image not in recent_images

    def test_manager_delegates_to_queryset_optimized_for_list(
        self, product_image
    ):
        optimized_images = ProductImage.objects.optimized_for_list()
        image = optimized_images.get(id=product_image.id)

        assert hasattr(image, "product")

    def test_manager_delegates_to_queryset_with_product_data(
        self, product_image
    ):
        images_with_data = ProductImage.objects.with_product_data()
        image = images_with_data.get(id=product_image.id)

        assert hasattr(image, "product")

    def test_manager_delegates_to_queryset_get_products_needing_images(self):
        product1 = ProductFactory()
        ProductImageFactory.create_batch(3, product=product1)

        product2 = ProductFactory()
        ProductImageFactory.create_batch(6, product=product2)

        products_needing_images = (
            ProductImage.objects.get_products_needing_images()
        )

        assert product1 in products_needing_images
        assert product2 not in products_needing_images

    def test_manager_delegates_to_queryset_get_products_without_main_image(
        self,
    ):
        product_with_main = ProductFactory()
        ProductImageFactory(product=product_with_main, is_main=True)

        product_without_main = ProductFactory()
        ProductImageFactory(product=product_without_main, is_main=False)

        products_without_main = (
            ProductImage.objects.get_products_without_main_image()
        )

        assert product_with_main not in products_without_main
        assert product_without_main in products_without_main

    def test_manager_delegates_to_queryset_bulk_update_sort_order(
        self, product
    ):
        image1 = ProductImageFactory(product=product, sort_order=1)
        image2 = ProductImageFactory(product=product, sort_order=2)

        image_orders = {
            image1.id: 10,
            image2.id: 20,
        }

        updated_count = ProductImage.objects.bulk_update_sort_order(
            image_orders
        )

        assert updated_count == 2

        image1.refresh_from_db()
        image2.refresh_from_db()

        assert image1.sort_order == 10
        assert image2.sort_order == 20

    def test_empty_queryset_methods(self):
        assert ProductImage.objects.main_images().count() == 0
        assert ProductImage.objects.secondary_images().count() == 0
        assert ProductImage.objects.with_titles().count() == 0
        assert ProductImage.objects.without_titles().count() == 0
        assert ProductImage.objects.recent().count() == 0

    def test_chained_filters(self, product_image):
        chained_images = (
            ProductImage.objects.for_product(product_image.product)
            .main_images()
            .recent()
            .optimized_for_list()
        )

        assert product_image in chained_images

    def test_complex_title_filtering(self):
        ProductImageTranslation = apps.get_model(
            "product", "ProductImageTranslation"
        )

        image_with_empty_title = ProductImageFactory(product=ProductFactory())
        image_with_empty_title.translations.all().delete()
        ProductImageTranslation.objects.create(
            master=image_with_empty_title, language_code="el", title=""
        )

        image_with_none_title = ProductImageFactory(product=ProductFactory())
        image_with_none_title.translations.all().delete()
        ProductImageTranslation.objects.create(
            master=image_with_none_title, language_code="el", title=None
        )

        image_with_whitespace_title = ProductImageFactory(
            product=ProductFactory()
        )
        image_with_whitespace_title.translations.all().delete()
        ProductImageTranslation.objects.create(
            master=image_with_whitespace_title, language_code="el", title="   "
        )

        images_without_titles = ProductImage.objects.without_titles()

        assert image_with_empty_title in images_without_titles
        assert image_with_none_title in images_without_titles
        assert image_with_whitespace_title not in images_without_titles

    def test_multiple_products_filtering(self):
        product1 = ProductFactory()
        product2 = ProductFactory()
        product3 = ProductFactory()

        image1 = ProductImageFactory(product=product1, is_main=True)
        image2 = ProductImageFactory(product=product2, is_main=False)
        image3 = ProductImageFactory(product=product3, is_main=True)

        product_ids = [product1.pk, product3.pk]
        images = ProductImage.objects.for_products(product_ids)

        assert image1 in images
        assert image3 in images
        assert image2 not in images

        main_images = ProductImage.objects.main_images()
        assert image1 in main_images
        assert image3 in main_images
        assert image2 not in main_images

    def test_ordering_consistency(self, product):
        base_time = timezone.now()

        image1 = ProductImageFactory(product=product, sort_order=1)
        ProductImage.objects.filter(id=image1.id).update(
            created_at=base_time - timedelta(minutes=2)
        )

        image2 = ProductImageFactory(product=product, sort_order=1)
        ProductImage.objects.filter(id=image2.id).update(
            created_at=base_time - timedelta(minutes=1)
        )

        ordered_images = list(ProductImage.objects.ordered_by_position())

        image1_index = next(
            i for i, img in enumerate(ordered_images) if img.id == image1.id
        )
        image2_index = next(
            i for i, img in enumerate(ordered_images) if img.id == image2.id
        )

        assert image1_index < image2_index

    def test_bulk_update_partial_success(self, product):
        existing_image = ProductImageFactory(product=product, sort_order=1)

        image_orders = {
            existing_image.id: 10,
            99999: 20,
        }

        updated_count = ProductImage.objects.bulk_update_sort_order(
            image_orders
        )

        assert updated_count == 1

        existing_image.refresh_from_db()
        assert existing_image.sort_order == 10
