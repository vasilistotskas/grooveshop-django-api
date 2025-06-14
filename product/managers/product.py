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

    def with_discount_value(self):
        return self.annotate(
            discount_value_amount=F("price")
            * F("discount_percent")
            / Value(100, output_field=DecimalField())
        )

    def with_vat_value(self):
        return self.annotate(
            vat_value_amount=Case(
                When(
                    vat__isnull=False,
                    then=F("price")
                    * F("vat__value")
                    / Value(100, output_field=DecimalField()),
                ),
                default=Value(0, output_field=DecimalField()),
            )
        )

    def with_final_price(self):
        queryset = self.with_discount_value().with_vat_value()
        return queryset.annotate(
            final_price_amount=F("price")
            + F("vat_value_amount")
            - F("discount_value_amount")
        )

    def with_price_save_percent(self):
        queryset = self.with_discount_value()
        return queryset.annotate(
            price_save_percent_field=Case(
                When(
                    price__gt=0,
                    then=F("discount_value_amount")
                    / F("price")
                    * Value(100, output_field=DecimalField()),
                ),
                default=Value(0, output_field=DecimalField()),
            )
        )

    def with_likes_count(self):
        likes_subquery = (
            ProductFavourite.objects.filter(product_id=OuterRef("pk"))
            .values("product_id")
            .annotate(count=Count("id"))
            .values("count")
        )

        return self.annotate(
            likes_count_field=Coalesce(Subquery(likes_subquery), Value(0))
        )

    def with_review_average(self):
        reviews_avg_subquery = (
            ProductReview.objects.filter(product_id=OuterRef("pk"))
            .values("product_id")
            .annotate(
                avg=Coalesce(Avg("rate"), Value(0, output_field=FloatField()))
            )
            .values("avg")
        )

        return self.annotate(
            review_average_field=Coalesce(
                Subquery(reviews_avg_subquery),
                Value(0, output_field=FloatField()),
            )
        )

    def with_all_annotations(self):
        return (
            self.with_final_price()
            .with_price_save_percent()
            .with_likes_count()
            .with_review_average()
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

    def with_discount_value(self):
        return self.get_queryset().with_discount_value()

    def with_vat_value(self):
        return self.get_queryset().with_vat_value()

    def with_final_price(self):
        return self.get_queryset().with_final_price()

    def with_price_save_percent(self):
        return self.get_queryset().with_price_save_percent()

    def with_likes_count(self):
        return self.get_queryset().with_likes_count()

    def with_review_average(self):
        return self.get_queryset().with_review_average()

    def with_all_annotations(self):
        return self.get_queryset().with_all_annotations()
