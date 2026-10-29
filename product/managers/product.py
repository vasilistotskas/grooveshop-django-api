from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django.db.models import (
    Case,
    DecimalField,
    F,
    FloatField,
    Func,
    IntegerField,
    OuterRef,
    Prefetch,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Cast, Coalesce, Floor

from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)
from core.mixins import SoftDeleteQuerySetMixin
from product.enum.review import ReviewStatus
from product.managers.image import ORDERED_IMAGES

if TYPE_CHECKING:
    from typing import Self


def likes_count_expression(product: str = "pk") -> Coalesce:
    """The number of favourites of a product, as a correlated subquery.

    ``product`` names the outer column holding the product id (``"pk"`` on
    ``Product``, ``"master_id"`` on ``ProductTranslation``).

    A subquery rather than ``Count("favourited_by")``: a join plus
    aggregate puts every other annotation into the GROUP BY, so the
    promotion subplan of ``with_offer_kind`` ran once per joined
    favourite x review row instead of once per product (a listing of 12
    products took 1.9 s against 0.15 s without the offer).
    """
    from product.models.favourite import ProductFavourite

    return Coalesce(
        Subquery(
            ProductFavourite._base_manager.filter(product=OuterRef(product))
            .order_by()
            .annotate(n=Func(F("pk"), function="COUNT"))
            .values("n"),
            output_field=IntegerField(),
        ),
        0,
    )


def _approved_reviews(product: str):
    from product.models.review import ProductReview

    return ProductReview._base_manager.filter(
        product=OuterRef(product), status=ReviewStatus.TRUE
    ).order_by()


def reviews_count_expression(product: str = "pk") -> Coalesce:
    """The number of approved reviews of a product (see ``likes_count_expression``)."""
    return Coalesce(
        Subquery(
            _approved_reviews(product)
            .annotate(n=Func(F("pk"), function="COUNT"))
            .values("n"),
            output_field=IntegerField(),
        ),
        0,
    )


def review_average_expression(product: str = "pk") -> Coalesce:
    """The average rate of approved reviews, 0.0 without any."""
    return Coalesce(
        Subquery(
            _approved_reviews(product)
            .annotate(avg=Func(F("rate"), function="AVG"))
            .values("avg"),
            output_field=FloatField(),
        ),
        Value(0.0, output_field=FloatField()),
    )


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
        return self.annotate(likes_count=likes_count_expression())

    def with_reviews_count(self) -> Self:
        """Annotate with the count of approved reviews.

        Approved only, like every public review surface
        (``ProductReviewQuerySet.visible_to``): a pending or rejected
        review must not move the number a shopper sees.
        """
        return self.annotate(reviews_count=reviews_count_expression())

    def with_review_average(self) -> Self:
        """Annotate with the average rating of approved reviews."""
        return self.annotate(review_average=review_average_expression())

    def active(self) -> Self:
        return self.filter(active=True).exclude_deleted()

    def inactive(self) -> Self:
        return self.filter(active=False)

    def in_stock(self) -> Self:
        return self.filter(stock__gt=0)

    def out_of_stock(self) -> Self:
        return self.filter(stock=0)

    def with_category(self) -> Self:
        """Select related category, VAT and brand."""
        return self.select_related("category", "vat", "brand")

    def with_main_image(self) -> Self:
        """Prefetch only the main image to avoid N+1 in main_image_path."""
        from product.models.image import ProductImage

        return self.prefetch_related(
            Prefetch(
                "images",
                queryset=ProductImage.objects.filter(is_main=True),
                to_attr="_prefetched_main_images",
            )
        )

    def with_ordered_images(self) -> Self:
        """Prefetch every image in display order (main first).

        Feeds ``Product.image_paths`` and ``Product.main_image_path``
        from one query, so a serializer that shows both stays O(1).
        """
        from product.models.image import ProductImage

        return self.prefetch_related(
            Prefetch(
                "images",
                queryset=ProductImage.objects.order_by(*ORDERED_IMAGES),
                to_attr="_prefetched_images",
            )
        )

    def with_variant_group(self) -> Self:
        """Select the variant group and prefetch its translations, for
        ``variant_group_title``."""
        return self.select_related("variant_group").prefetch_related(
            "variant_group__translations"
        )

    def with_tags(self) -> Self:
        """Prefetch product tags with translations."""
        return self.prefetch_related("tags__tag__translations")

    def with_product_attributes(self) -> Self:
        """Prefetch product attributes with values and translations."""
        return self.prefetch_related(
            Prefetch(
                "product_attributes",
                queryset=(
                    self.model.product_attributes.rel.related_model.objects.select_related(
                        "attribute_value__attribute"
                    ).prefetch_related(
                        "attribute_value__translations",
                        "attribute_value__attribute__translations",
                    )
                ),
            )
        )

    def with_discount_value_annotation(self) -> Self:
        """The markdown on the NET price (``Product.discount_value``)."""
        return self.annotate(
            discount_value_annotation=F("price")
            * F("discount_percent")
            / Value(100, output_field=DecimalField())
        )

    def with_vat_value_annotation(self) -> Self:
        """VAT of the price actually charged (``Product.vat_value``):
        on the net price after the markdown."""
        hundred = Value(100, output_field=DecimalField())
        return self.annotate(
            vat_value_annotation=Case(
                When(
                    vat__isnull=False,
                    then=F("price")
                    * (hundred - F("discount_percent"))
                    / hundred
                    * F("vat__value")
                    / hundred,
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

    def with_announced_discount_percent(self) -> Self:
        """Annotate ``announced_discount_percent``: SQL twin of the property.

        Whole percent between ``prior_price`` and ``effective_price``,
        floored, ``NULL`` unless the product announces a reduction. Reads
        the stored columns only, so the catalogue can sort and filter on it.
        """
        return self.annotate(
            announced_discount_percent=Case(
                When(
                    prior_price__gt=0,
                    effective_price__isnull=False,
                    effective_price__lt=F("prior_price"),
                    then=Cast(
                        Floor(
                            (F("prior_price") - F("effective_price"))
                            * Value(100, output_field=DecimalField())
                            / F("prior_price")
                        ),
                        output_field=IntegerField(),
                    ),
                ),
                default=None,
                output_field=IntegerField(),
            )
        )

    def with_offer_kind(self) -> Self:
        """Annotate ``offer_kind_annotation``: MARKDOWN, PROMOTION or NULL.

        Opt-in rather than part of ``for_list()``: only the surfaces that
        render the offer badge pay for the promotion subquery.
        """
        from promotion.offers import offer_kind_expression

        return self.annotate(offer_kind_annotation=offer_kind_expression())

    def with_counts(self) -> Self:
        """Annotate with all count fields for efficient property access."""
        return (
            self.with_likes_count().with_review_average().with_reviews_count()
        )

    def for_list(self) -> Self:
        """
        Optimized queryset for ``ProductCardSerializer``: a product card.

        Translations, category/VAT/brand, the counts and the main image.
        The counts stay although the card shows only the review ones:
        the list sorts on ``likes_count`` too. Only returns active,
        non-deleted products.
        """
        return (
            self.active()
            .with_translations()
            .with_category()
            .with_counts()
            .with_announced_discount_percent()
            .with_main_image()
        )

    def for_full_list(self) -> Self:
        """
        Optimized queryset for a list rendered by ``ProductSerializer``.

        ``for_list()`` plus every image, the variant group's translations
        and the attributes: what the catalogue feed (``/product/feed``) and
        the hydrated recommendations render. Only returns active,
        non-deleted products.
        """
        return (
            self.active()
            .with_translations()
            .with_category()
            .with_counts()
            .with_announced_discount_percent()
            .with_ordered_images()
            .with_variant_group()
            .with_product_attributes()
        )

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Includes translations, category, counts, images, attributes and tags.
        Does NOT filter by active status - allows viewing/editing any product by ID.
        """
        return (
            self.exclude_deleted()
            .with_translations()
            .with_category()
            .with_counts()
            .with_ordered_images()
            .with_variant_group()
            .with_product_attributes()
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
        return cast(ProductQuerySet, self.get_queryset().for_list())

    def for_full_list(self) -> ProductQuerySet:
        """Return optimized queryset for lists of full product rows."""
        return cast(ProductQuerySet, self.get_queryset().for_full_list())

    def for_detail(self) -> ProductQuerySet:
        """Return optimized queryset for detail views."""
        return cast(ProductQuerySet, self.get_queryset().for_detail())
