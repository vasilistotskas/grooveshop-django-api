from datetime import timedelta

from django.db import models
from django.db.models import Count, F, Sum
from django.utils import timezone
from djmoney.money import Money


class OrderItemQuerySet(models.QuerySet):
    def for_order(self, order):
        return self.filter(order=order)

    def for_product(self, product):
        return self.filter(product=product)

    def with_product_data(self):
        return self.select_related("product").prefetch_related(
            "product__translations"
        )

    def sum_quantities(self):
        return (
            self.aggregate(total_quantity=Sum("quantity"))["total_quantity"]
            or 0
        )

    def annotate_total_price(self):
        return self.annotate(calculated_total=F("price") * F("quantity"))

    def total_items_cost(self):
        items = self.annotate_total_price()
        total = items.aggregate(total=Sum("calculated_total"))["total"] or 0

        first_item = self.first()
        if first_item and hasattr(first_item, "price"):
            return Money(amount=total, currency=first_item.price.currency)
        return Money(amount=0, currency="USD")

    def for_user(self, user):
        return self.filter(order__user=user)

    def refunded(self):
        return self.filter(is_refunded=True)

    def not_refunded(self):
        return self.filter(is_refunded=False)

    def recent(self, days=30):
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.filter(order__created_at__gte=cutoff_date)


class OrderItemManager(models.Manager):
    def get_queryset(self) -> OrderItemQuerySet:
        return OrderItemQuerySet(self.model, using=self._db)

    def for_product(self, product):
        return self.get_queryset().for_product(product)

    def for_order(self, order):
        return self.get_queryset().for_order(order)

    def with_product_data(self):
        return self.get_queryset().with_product_data()

    def sum_quantities(self):
        return self.get_queryset().sum_quantities()

    def total_items_cost(self):
        return self.get_queryset().total_items_cost()

    def for_user(self, user):
        return self.get_queryset().for_user(user)

    def refunded(self):
        return self.get_queryset().refunded()

    def not_refunded(self):
        return self.get_queryset().not_refunded()

    def recent(self, days=30):
        return self.get_queryset().recent(days)

    def get_bestselling_products(self, limit=10, days=30):
        return (
            self.recent(days)
            .values("product__id", "product__name")
            .annotate(
                total_quantity=Sum("quantity"),
                total_revenue=Sum(F("price") * F("quantity")),
                order_count=Count("order", distinct=True),
            )
            .order_by("-total_quantity")[:limit]
        )
