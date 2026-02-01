from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models
from django.db.models import Count, Q
from django.utils import timezone
from parler.managers import TranslatableManager, TranslatableQuerySet

if TYPE_CHECKING:
    from typing import Self


class EnhancedImageQuerySet(TranslatableQuerySet):
    """
    Optimized QuerySet for ProductImage model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_translations(self) -> Self:
        """Prefetch translations for better performance."""
        return self.prefetch_related("translations")

    def with_product(self) -> Self:
        """Select related product."""
        return self.select_related("product")

    def with_product_translations(self) -> Self:
        """Prefetch product translations."""
        return self.prefetch_related("product__translations")

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes product and translations.
        """
        return self.with_translations().with_product().ordered_by_position()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Includes everything from for_list() plus product translations.
        """
        return self.for_list().with_product_translations()

    def optimized_for_list(self) -> Self:
        """Alias for for_list() for backward compatibility."""
        return self.for_list()

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

    def get_products_needing_images(self, max_images=5):
        from product.models.product import Product  # noqa: PLC0415

        return Product.objects.annotate(image_count=Count("images")).filter(
            image_count__lt=max_images
        )

    def get_products_without_main_image(self):
        from django.db.models import Count  # noqa: PLC0415
        from product.models.product import Product  # noqa: PLC0415

        products_with_main_ids = set(
            self.main_images().values_list("product", flat=True)
        )

        products_with_any_images_ids = set(
            self.values_list("product", flat=True).distinct()
        )

        products_without_main_ids = (
            products_with_any_images_ids - products_with_main_ids
        )

        products_no_images_qs = Product.objects.annotate(
            image_count=Count("images")
        ).filter(image_count=0)

        combined_ids = list(products_without_main_ids) + list(
            products_no_images_qs.values_list("id", flat=True)
        )

        return Product.objects.filter(id__in=combined_ids)

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
    """
    Manager for ProductImage model with optimized queryset methods.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return ProductImage.objects.for_list()
            return ProductImage.objects.for_detail()
    """

    def get_queryset(self) -> EnhancedImageQuerySet:
        return EnhancedImageQuerySet(self.model, using=self._db)

    def for_list(self) -> EnhancedImageQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> EnhancedImageQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()

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

    def ordered_by_position(self):
        return self.get_queryset().ordered_by_position()

    def recent(self, days=7):
        return self.get_queryset().recent(days)

    def optimized_for_list(self):
        """Alias for for_list() for backward compatibility."""
        return self.get_queryset().for_list()

    def with_product_data(self):
        """Return queryset with product data prefetched."""
        return self.get_queryset().with_product().with_product_translations()

    def get_products_needing_images(self, max_images=5):
        return self.get_queryset().get_products_needing_images(max_images)

    def get_products_without_main_image(self):
        return self.get_queryset().get_products_without_main_image()

    def bulk_update_sort_order(self, image_orders):
        return self.get_queryset().bulk_update_sort_order(image_orders)
