from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.db.models import Count, F, Sum
from django.utils import timezone
from djmoney.money import Money

from core.managers import OptimizedManager, OptimizedQuerySet

if TYPE_CHECKING:
    from typing import Self


class OrderItemQuerySet(OptimizedQuerySet):
    """Optimized QuerySet for OrderItem model."""

    def for_order(self, order) -> Self:
        return self.filter(order=order)

    def for_product(self, product) -> Self:
        return self.filter(product=product)

    def with_product_data(self) -> Self:
        return self.select_related("product").prefetch_related(
            "product__translations"
        )

    def sum_quantities(self):
        return (
            self.aggregate(total_quantity=Sum("quantity"))["total_quantity"]
            or 0
        )

    def annotate_total_price(self) -> Self:
        return self.annotate(calculated_total=F("price") * F("quantity"))

    def total_items_cost(self):
        items = self.annotate_total_price()
        total = items.aggregate(total=Sum("calculated_total"))["total"] or 0

        first_item = self.first()
        if first_item and hasattr(first_item, "price"):
            return Money(amount=total, currency=first_item.price.currency)
        return Money(amount=0, currency=settings.DEFAULT_CURRENCY)

    def for_user(self, user) -> Self:
        return self.filter(order__user=user)

    def refunded(self) -> Self:
        return self.filter(is_refunded=True)

    def not_refunded(self) -> Self:
        return self.filter(is_refunded=False)

    def recent(self, days=30) -> Self:
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.filter(order__created_at__gte=cutoff_date)

    def for_list(self) -> Self:
        """Return optimized queryset for list views."""
        return self.select_related("order", "product")

    def for_detail(self) -> Self:
        """Return optimized queryset for detail views."""
        return self.for_list().with_product_data()


class OrderItemManager(OptimizedManager):
    """Manager for OrderItem model."""

    queryset_class = OrderItemQuerySet

    def for_list(self) -> OrderItemQuerySet:
        return self.get_queryset().for_list()

    def for_detail(self) -> OrderItemQuerySet:
        return self.get_queryset().for_detail()

    def get_bestselling_products(self, limit=10, days=30):
        return (
            self.get_queryset()
            .recent(days)
            .values("product__id", "product__name")
            .annotate(
                total_quantity=Sum("quantity"),
                total_revenue=Sum(F("price") * F("quantity")),
                order_count=Count("order", distinct=True),
            )
            .order_by("-total_quantity")[:limit]
        )
