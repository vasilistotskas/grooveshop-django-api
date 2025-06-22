from __future__ import annotations

from django.db import models
from django.db.models import Count, Q
from django.utils import timezone
from parler.managers import TranslatableManager, TranslatableQuerySet


class EnhancedImageQuerySet(TranslatableQuerySet):
    def main_images(self):
        return self.filter(is_main=True)

    def secondary_images(self):
        return self.filter(is_main=False)

    def for_product(self, product):
        if hasattr(product, "pk"):
            return self.filter(product=product.pk)
        return self.filter(product=product)

    def for_products(self, product_ids):
        return self.filter(product__in=product_ids)

    def with_titles(self):
        return self.translated().exclude(
            Q(translations__title__isnull=True) | Q(translations__title="")
        )

    def without_titles(self):
        return self.translated().filter(
            Q(translations__title__isnull=True) | Q(translations__title="")
        )

    def recent(self, days=7):
        cutoff = timezone.now() - timezone.timedelta(days=days)
        return self.filter(created_at__gte=cutoff)

    def ordered_by_position(self):
        return self.order_by("sort_order", "created_at")

    def optimized_for_list(self):
        return self.select_related("product").prefetch_related("translations")

    def with_product_data(self):
        return self.select_related("product").prefetch_related(
            "product__translations", "translations"
        )

    def get_products_needing_images(self, max_images=5):
        from product.models.product import Product  # noqa: PLC0415

        products_with_few_images = (
            self.values("product")
            .annotate(image_count=Count("id"))
            .filter(image_count__lt=max_images)
            .values_list("product", flat=True)
        )

        return Product.objects.filter(id__in=products_with_few_images)

    def get_products_without_main_image(self):
        from product.models.product import Product  # noqa: PLC0415

        products_with_main = self.main_images().values_list(
            "product", flat=True
        )
        return Product.objects.exclude(id__in=products_with_main)

    def bulk_update_sort_order(self, image_orders):
        cases = []
        ids = []

        for image_id, sort_order in image_orders.items():
            cases.append(models.When(id=image_id, then=sort_order))
            ids.append(image_id)

        if cases:
            return self.filter(id__in=ids).update(
                sort_order=models.Case(*cases, default=models.F("sort_order"))
            )
        return 0


class EnhancedImageManager(TranslatableManager):
    def get_queryset(self) -> EnhancedImageQuerySet:
        return EnhancedImageQuerySet(self.model, using=self._db)

    def main_image(self, product):
        return self.get_queryset().main_images().for_product(product).first()

    def main_images(self):
        return self.get_queryset().main_images()

    def secondary_images(self):
        return self.get_queryset().secondary_images()

    def for_product(self, product):
        return self.get_queryset().for_product(product)

    def for_products(self, product_ids):
        return self.get_queryset().for_products(product_ids)

    def with_titles(self):
        return self.get_queryset().with_titles()

    def without_titles(self):
        return self.get_queryset().without_titles()

    def recent(self, days=7):
        return self.get_queryset().recent(days)

    def optimized_for_list(self):
        return self.get_queryset().optimized_for_list()

    def with_product_data(self):
        return self.get_queryset().with_product_data()

    def get_products_needing_images(self, max_images=5):
        return self.get_queryset().get_products_needing_images(max_images)

    def get_products_without_main_image(self):
        return self.get_queryset().get_products_without_main_image()

    def bulk_update_sort_order(self, image_orders):
        return self.get_queryset().bulk_update_sort_order(image_orders)

    def ordered_by_position(self):
        return self.get_queryset().ordered_by_position()
