from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Count, ExpressionWrapper, F, Sum
from djmoney.models.fields import MoneyField

from core.managers import OptimizedManager, OptimizedQuerySet
from core.mixins import SoftDeleteQuerySetMixin
from order.enum.status import OrderStatus

if TYPE_CHECKING:
    from typing import Self


class OrderQuerySet(SoftDeleteQuerySetMixin, OptimizedQuerySet):
    """
    Optimized QuerySet for Order model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_user(self) -> Self:
        """Select related user for order."""
        return self.select_related("user")

    def with_payment_info(self) -> Self:
        """Select related payment and location info."""
        return self.select_related("pay_way", "country", "region")

    def with_items(self) -> Self:
        """Prefetch order items with product data."""
        return self.prefetch_related(
            "items__product__translations",
            "items__product__images__translations",
        )

    def with_items_basic(self) -> Self:
        """Prefetch order items without deep product data (for list views)."""
        return self.prefetch_related("items", "items__product__translations")

    def with_counts(self) -> Self:
        """Annotate with item counts for efficient property access."""
        return self.annotate(
            _items_count=Count("items", distinct=True),
            _total_quantity=Sum("items__quantity"),
        )

    def with_total_amounts(self) -> Self:
        """Annotate with calculated total amounts."""
        return self.annotate(
            items_total=Sum(
                ExpressionWrapper(
                    F("items__price") * F("items__quantity"),
                    output_field=MoneyField(max_digits=11, decimal_places=2),
                )
            )
        )

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes user, payment info, and counts but not full item details.
        """
        return (
            self.exclude_deleted()
            .with_user()
            .with_payment_info()
            .with_items_basic()
            .with_counts()
        )

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Includes everything from for_list() plus full item details.
        """
        return (
            self.exclude_deleted()
            .with_user()
            .with_payment_info()
            .with_items()
            .with_counts()
        )

    # Status filter methods
    def pending(self) -> Self:
        return self.filter(status=OrderStatus.PENDING)

    def processing(self) -> Self:
        return self.filter(status=OrderStatus.PROCESSING)

    def shipped(self) -> Self:
        return self.filter(status=OrderStatus.SHIPPED)

    def delivered(self) -> Self:
        return self.filter(status=OrderStatus.DELIVERED)

    def completed(self) -> Self:
        return self.filter(status=OrderStatus.COMPLETED)

    def canceled(self) -> Self:
        return self.filter(status=OrderStatus.CANCELED)

    def returned(self) -> Self:
        return self.filter(status=OrderStatus.RETURNED)

    def refunded(self) -> Self:
        return self.filter(status=OrderStatus.REFUNDED)


class OrderManager(OptimizedManager):
    """
    Manager for Order model with optimized queryset methods.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return Order.objects.for_list()
            return Order.objects.for_detail()
    """

    queryset_class = OrderQuerySet

    def get_queryset(self) -> OrderQuerySet:
        """Return base queryset excluding deleted orders."""
        return OrderQuerySet(self.model, using=self._db).exclude_deleted()

    def for_list(self) -> OrderQuerySet:
        """Return optimized queryset for list views."""
        return OrderQuerySet(self.model, using=self._db).for_list()

    def for_detail(self) -> OrderQuerySet:
        """Return optimized queryset for detail views."""
        return OrderQuerySet(self.model, using=self._db).for_detail()
