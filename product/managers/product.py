from __future__ import annotations

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


class ProductQuerySet(TranslatableQuerySet, SoftDeleteQuerySet):
    def active(self):
        return self.filter(active=True).exclude_deleted()

    def inactive(self):
        return self.filter(active=False)

    def in_stock(self):
        return self.filter(stock__gt=0)

    def out_of_stock(self):
        return self.filter(stock=0)

    def exclude_deleted(self):
        return self.exclude(is_deleted=True)

    def with_discount_value_annotation(self):
        return self.annotate(
            discount_value_annotation=F("price")
            * F("discount_percent")
            / Value(100, output_field=DecimalField())
        )

    def with_vat_value_annotation(self):
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

    def with_final_price_annotation(self):
        queryset = (
            self.with_discount_value_annotation().with_vat_value_annotation()
        )
        return queryset.annotate(
            final_price_annotation=F("price")
            + F("vat_value_annotation")
            - F("discount_value_annotation")
        )

    def with_price_save_percent_annotation(self):
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

    def with_likes_count_annotation(self):
        likes_subquery = (
            ProductFavourite.objects.filter(product_id=OuterRef("pk"))
            .values("product_id")
            .annotate(count=Count("id"))
            .values("count")
        )

        return self.annotate(
            likes_count_annotation=Coalesce(Subquery(likes_subquery), Value(0))
        )

    def with_review_average_annotation(self):
        reviews_avg_subquery = (
            ProductReview.objects.filter(product_id=OuterRef("pk"))
            .values("product_id")
            .annotate(
                avg=Coalesce(Avg("rate"), Value(0, output_field=FloatField()))
            )
            .values("avg")
        )

        return self.annotate(
            review_average_annotation=Coalesce(
                Subquery(reviews_avg_subquery),
                Value(0, output_field=FloatField()),
            )
        )


class ProductManager(TranslatableManager):
    def get_queryset(self) -> ProductQuerySet:
        return ProductQuerySet(self.model, using=self._db).exclude_deleted()

    def active(self):
        return self.get_queryset().active()

    def inactive(self):
        return self.get_queryset().inactive()

    def in_stock(self):
        return self.get_queryset().in_stock()

    def out_of_stock(self):
        return self.get_queryset().out_of_stock()

    def with_discount_value_annotation(self):
        return self.get_queryset().with_discount_value_annotation()

    def with_vat_value_annotation(self):
        return self.get_queryset().with_vat_value_annotation()

    def with_final_price_annotation(self):
        return self.get_queryset().with_final_price_annotation()

    def with_price_save_percent_annotation(self):
        return self.get_queryset().with_price_save_percent_annotation()

    def with_likes_count_annotation(self):
        return self.get_queryset().with_likes_count_annotation()

    def with_review_average_annotation(self):
        return self.get_queryset().with_review_average_annotation()
