from typing import cast

from django.db import models
from django.db.models import ExpressionWrapper, F, QuerySet, Sum
from djmoney.models.fields import MoneyField

from order.enum.status import OrderStatus


class OrderQuerySet(models.QuerySet):
    def with_total_amounts(self) -> QuerySet:
        return self.annotate(
            items_total=Sum(
                ExpressionWrapper(
                    F("items__price") * F("items__quantity"),
                    output_field=MoneyField(max_digits=11, decimal_places=2),
                )
            )
        )

    def pending(self) -> QuerySet:
        return self.filter(status=OrderStatus.PENDING)

    def processing(self) -> QuerySet:
        return self.filter(status=OrderStatus.PROCESSING)

    def shipped(self) -> QuerySet:
        return self.filter(status=OrderStatus.SHIPPED)

    def delivered(self) -> QuerySet:
        return self.filter(status=OrderStatus.DELIVERED)

    def completed(self) -> QuerySet:
        return self.filter(status=OrderStatus.COMPLETED)

    def canceled(self) -> QuerySet:
        return self.filter(status=OrderStatus.CANCELED)

    def returned(self) -> QuerySet:
        return self.filter(status=OrderStatus.RETURNED)

    def refunded(self) -> QuerySet:
        return self.filter(status=OrderStatus.REFUNDED)


class OrderManager(models.Manager):
    def get_queryset(self) -> OrderQuerySet:
        return cast(
            "OrderQuerySet",
            OrderQuerySet(self.model, using=self._db)
            .select_related("user", "pay_way", "country", "region")
            .prefetch_related("items", "items__product")
            .exclude(is_deleted=True),
        )

    def with_total_amounts(self) -> QuerySet:
        return self.get_queryset().with_total_amounts()

    def pending(self) -> QuerySet:
        return self.get_queryset().pending()

    def processing(self) -> QuerySet:
        return self.get_queryset().processing()

    def shipped(self) -> QuerySet:
        return self.get_queryset().shipped()

    def delivered(self) -> QuerySet:
        return self.get_queryset().delivered()

    def completed(self) -> QuerySet:
        return self.get_queryset().completed()

    def canceled(self) -> QuerySet:
        return self.get_queryset().canceled()

    def returned(self) -> QuerySet:
        return self.get_queryset().returned()

    def refunded(self) -> QuerySet:
        return self.get_queryset().refunded()
