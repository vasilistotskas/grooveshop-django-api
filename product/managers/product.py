from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import (
    Avg,
    Case,
    Count,
    DecimalField,
    F,
    FloatField,
    OuterRef,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from parler.managers import TranslatableManager, TranslatableQuerySet

from core.models import (
    SoftDeleteQuerySet,
)
from product.models.favourite import ProductFavourite
from product.models.review import ProductReview

if TYPE_CHECKING:
    from typing import Self


class ProductQuerySet(TranslatableQuerySet, SoftDeleteQuerySet):
    """
    Optimized QuerySet for Product model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def active(self) -> Self:
        return self.filter(active=True).exclude_deleted()

    def inactive(self) -> Self:
        return self.filter(active=False)

    def in_stock(self) -> Self:
        return self.filter(stock__gt=0)

    def out_of_stock(self) -> Self:
        return self.filter(stock=0)

    def exclude_deleted(self) -> Self:
        return self.exclude(is_deleted=True)

    def with_translations(self) -> Self:
        """Prefetch translations for better performance."""
        return self.prefetch_related("translations")

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

    def with_likes_count_annotation(self) -> Self:
        """Annotate with likes count for efficient property access."""
        likes_subquery = (
            ProductFavourite.objects.filter(product_id=OuterRef("pk"))
            .values("product_id")
            .annotate(count=Count("id"))
            .values("count")
        )

        return self.annotate(
            _likes_count=Coalesce(Subquery(likes_subquery), Value(0)),
            likes_count_annotation=Coalesce(Subquery(likes_subquery), Value(0)),
        )

    def with_review_average_annotation(self) -> Self:
        """Annotate with review average for efficient property access."""
        reviews_avg_subquery = (
            ProductReview.objects.filter(product_id=OuterRef("pk"))
            .values("product_id")
            .annotate(
                avg=Coalesce(Avg("rate"), Value(0, output_field=FloatField()))
            )
            .values("avg")
        )

        return self.annotate(
            _review_average=Coalesce(
                Subquery(reviews_avg_subquery),
                Value(0, output_field=FloatField()),
            ),
            review_average_annotation=Coalesce(
                Subquery(reviews_avg_subquery),
                Value(0, output_field=FloatField()),
            ),
        )

    def with_reviews_count_annotation(self) -> Self:
        """Annotate with reviews count for efficient property access."""
        return self.annotate(_reviews_count=Count("reviews", distinct=True))

    def with_counts(self) -> Self:
        """Annotate with all count fields for efficient property access."""
        return (
            self.with_likes_count_annotation()
            .with_review_average_annotation()
            .with_reviews_count_annotation()
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


class ProductManager(TranslatableManager):
    """
    Manager for Product model with optimized queryset methods.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return Product.objects.for_list()
            return Product.objects.for_detail()
    """

    def get_queryset(self) -> ProductQuerySet:
        return ProductQuerySet(self.model, using=self._db).exclude_deleted()

    def for_list(self) -> ProductQuerySet:
        """Return optimized queryset for list views."""
        return ProductQuerySet(self.model, using=self._db).for_list()

    def for_detail(self) -> ProductQuerySet:
        """Return optimized queryset for detail views."""
        return ProductQuerySet(self.model, using=self._db).for_detail()

    def active(self) -> ProductQuerySet:
        return self.get_queryset().active()

    def inactive(self) -> ProductQuerySet:
        return self.get_queryset().inactive()

    def in_stock(self) -> ProductQuerySet:
        return self.get_queryset().in_stock()

    def out_of_stock(self) -> ProductQuerySet:
        return self.get_queryset().out_of_stock()

    def with_discount_value_annotation(self) -> ProductQuerySet:
        return self.get_queryset().with_discount_value_annotation()

    def with_vat_value_annotation(self) -> ProductQuerySet:
        return self.get_queryset().with_vat_value_annotation()

    def with_final_price_annotation(self) -> ProductQuerySet:
        return self.get_queryset().with_final_price_annotation()

    def with_price_save_percent_annotation(self) -> ProductQuerySet:
        return self.get_queryset().with_price_save_percent_annotation()

    def with_likes_count_annotation(self) -> ProductQuerySet:
        return self.get_queryset().with_likes_count_annotation()

    def with_review_average_annotation(self) -> ProductQuerySet:
        return self.get_queryset().with_review_average_annotation()
