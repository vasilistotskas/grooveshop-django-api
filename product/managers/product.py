from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import (
    Avg,
    Case,
    Count,
    DecimalField,
    F,
    FloatField,
    Value,
    When,
)
from django.db.models.functions import Coalesce

from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)
from core.mixins import SoftDeleteQuerySetMixin

if TYPE_CHECKING:
    from typing import Self


class ProductQuerySet(
    SoftDeleteQuerySetMixin,
    TranslatableOptimizedQuerySet,
):
    """
    Optimized QuerySet for Product model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_likes_count(self) -> Self:
        """Annotate with likes count from favourited_by relationship."""
        return self.annotate(likes_count=Count("favourited_by", distinct=True))

    def with_reviews_count(self) -> Self:
        """Annotate with reviews count."""
        return self.annotate(reviews_count=Count("reviews", distinct=True))

    def with_review_average(self) -> Self:
        """Annotate with average review rating."""
        return self.annotate(
            review_average=Coalesce(
                Avg("reviews__rate"),
                Value(0.0, output_field=FloatField()),
            )
        )

    def active(self) -> Self:
        return self.filter(active=True).exclude_deleted()

    def inactive(self) -> Self:
        return self.filter(active=False)

    def in_stock(self) -> Self:
        return self.filter(stock__gt=0)

    def out_of_stock(self) -> Self:
        return self.filter(stock=0)

    def with_category(self) -> Self:
        """Select related category and VAT."""
        return self.select_related("category", "vat")

    def with_images(self) -> Self:
        """Prefetch product images with translations."""
        return self.prefetch_related("images__translations")

    def with_tags(self) -> Self:
        """Prefetch product tags with translations."""
        return self.prefetch_related("tags__tag__translations")

    def with_discount_value_annotation(self) -> Self:
        return self.annotate(
            discount_value_annotation=F("price")
            * F("discount_percent")
            / Value(100, output_field=DecimalField())
        )

    def with_vat_value_annotation(self) -> Self:
        return self.annotate(
            vat_value_annotation=Case(
                When(
                    vat__isnull=False,
                    then=F("price")
                    * F("vat__value")
                    / Value(100, output_field=DecimalField()),
                ),
                default=Value(0, output_field=DecimalField()),
            )
        )

    def with_final_price_annotation(self) -> Self:
        queryset = (
            self.with_discount_value_annotation().with_vat_value_annotation()
        )
        return queryset.annotate(
            final_price_annotation=F("price")
            + F("vat_value_annotation")
            - F("discount_value_annotation")
        )

    def with_price_save_percent_annotation(self) -> Self:
        queryset = self.with_discount_value_annotation()
        return queryset.annotate(
            price_save_percent_annotation=Case(
                When(
                    price__gt=0,
                    then=F("discount_value_annotation")
                    / F("price")
                    * Value(100, output_field=DecimalField()),
                ),
                default=Value(0, output_field=DecimalField()),
            )
        )

    def with_counts(self) -> Self:
        """Annotate with all count fields for efficient property access."""
        return (
            self.with_likes_count().with_review_average().with_reviews_count()
        )

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes translations, category, and counts but not images/tags.
        Only returns active, non-deleted products.
        """
        return self.active().with_translations().with_category().with_counts()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Includes translations, category, counts, images and tags.
        Does NOT filter by active status - allows viewing/editing any product by ID.
        """
        return (
            self.exclude_deleted()
            .with_translations()
            .with_category()
            .with_counts()
            .with_images()
            .with_tags()
        )


class ProductManager(TranslatableOptimizedManager):
    """
    Manager for Product model with optimized queryset methods.

    Most methods are automatically delegated to ProductQuerySet
    via __getattr__. Only for_list() and for_detail() are explicitly
    defined for IDE support.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return Product.objects.for_list()
            return Product.objects.for_detail()
    """

    queryset_class = ProductQuerySet

    def for_list(self) -> ProductQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> ProductQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()
