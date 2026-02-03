from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.utils import timezone

from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)

if TYPE_CHECKING:
    from typing import Self


class OrderHistoryQuerySet(TranslatableOptimizedQuerySet):
    """Optimized QuerySet for OrderHistory model."""

    def for_order(self, order) -> Self:
        return self.filter(order=order)

    def by_change_type(self, change_type) -> Self:
        return self.filter(change_type=change_type)

    def by_user(self, user) -> Self:
        return self.filter(user=user)

    def recent(self, days=7) -> Self:
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=cutoff_date)

    def status_changes(self) -> Self:
        return self.filter(change_type="STATUS")

    def payment_changes(self) -> Self:
        return self.filter(change_type="PAYMENT")

    def system_changes(self) -> Self:
        return self.filter(user__isnull=True)

    def user_changes(self) -> Self:
        return self.filter(user__isnull=False)

    def for_list(self) -> Self:
        """Return optimized queryset for list views."""
        return self.with_translations().select_related("order", "user")

    def for_detail(self) -> Self:
        """Return optimized queryset for detail views."""
        return self.for_list()


class OrderHistoryManager(TranslatableOptimizedManager):
    """Manager for OrderHistory model."""

    queryset_class = OrderHistoryQuerySet

    def for_list(self) -> OrderHistoryQuerySet:
        return self.get_queryset().for_list()

    def for_detail(self) -> OrderHistoryQuerySet:
        return self.get_queryset().for_detail()


class OrderItemHistoryQuerySet(TranslatableOptimizedQuerySet):
    """Optimized QuerySet for OrderItemHistory model."""

    def for_order_item(self, order_item) -> Self:
        return self.filter(order_item=order_item)

    def for_order(self, order) -> Self:
        return self.filter(order_item__order=order)

    def by_change_type(self, change_type) -> Self:
        return self.filter(change_type=change_type)

    def quantity_changes(self) -> Self:
        return self.filter(change_type="QUANTITY")

    def price_changes(self) -> Self:
        return self.filter(change_type="PRICE")

    def refunds(self) -> Self:
        return self.filter(change_type="REFUND")

    def for_list(self) -> Self:
        """Return optimized queryset for list views."""
        return self.with_translations().select_related(
            "order_item", "order_item__order"
        )

    def for_detail(self) -> Self:
        """Return optimized queryset for detail views."""
        return self.for_list()


class OrderItemHistoryManager(TranslatableOptimizedManager):
    """Manager for OrderItemHistory model."""

    queryset_class = OrderItemHistoryQuerySet

    def for_list(self) -> OrderItemHistoryQuerySet:
        return self.get_queryset().for_list()

    def for_detail(self) -> OrderItemHistoryQuerySet:
        return self.get_queryset().for_detail()
