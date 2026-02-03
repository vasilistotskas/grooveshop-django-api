from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.utils import timezone

from core.managers import OptimizedManager, OptimizedQuerySet

if TYPE_CHECKING:
    from typing import Self


class StockLogQuerySet(OptimizedQuerySet):
    """QuerySet for StockLog model."""

    def by_product(self, product) -> Self:
        """Filter by product."""
        return self.filter(product=product)

    def by_operation(self, operation: str) -> Self:
        """Filter by operation type."""
        return self.filter(operation_type=operation)

    def recent(self, days: int = 30) -> Self:
        """Filter to recent logs within specified days."""
        cutoff = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=cutoff)

    def with_product(self) -> Self:
        """Select related product data."""
        return self.select_related("product")

    def with_order(self) -> Self:
        """Select related order data."""
        return self.select_related("order")

    def with_user(self) -> Self:
        """Select related user data."""
        return self.select_related("performed_by")

    def for_list(self) -> Self:
        """Return optimized queryset for list views."""
        return self.with_product().with_order()

    def for_detail(self) -> Self:
        """Return optimized queryset for detail views."""
        return self.for_list().with_user()


class StockLogManager(OptimizedManager):
    """Manager for StockLog model."""

    queryset_class = StockLogQuerySet

    def for_list(self) -> StockLogQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> StockLogQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()


class StockReservationQuerySet(OptimizedQuerySet):
    """QuerySet for StockReservation model."""

    def active(self) -> Self:
        """Filter to active (non-expired, non-consumed) reservations."""
        return self.filter(
            expires_at__gt=timezone.now(),
            consumed=False,
        )

    def expired(self) -> Self:
        """Filter to expired reservations."""
        return self.filter(expires_at__lte=timezone.now())

    def consumed(self) -> Self:
        """Filter to consumed reservations."""
        return self.filter(consumed=True)

    def unconsumed(self) -> Self:
        """Filter to unconsumed reservations."""
        return self.filter(consumed=False)

    def by_product(self, product) -> Self:
        """Filter by product."""
        return self.filter(product=product)

    def by_user(self, user) -> Self:
        """Filter by user."""
        return self.filter(reserved_by=user)

    def by_session(self, session_id: str) -> Self:
        """Filter by session ID."""
        return self.filter(session_id=session_id)

    def with_product(self) -> Self:
        """Select related product data."""
        return self.select_related("product")

    def with_user(self) -> Self:
        """Select related user data."""
        return self.select_related("reserved_by")

    def with_order(self) -> Self:
        """Select related order data."""
        return self.select_related("order")

    def for_list(self) -> Self:
        """Return optimized queryset for list views."""
        return self.with_product().with_user()

    def for_detail(self) -> Self:
        """Return optimized queryset for detail views."""
        return self.for_list().with_order()


class StockReservationManager(OptimizedManager):
    """Manager for StockReservation model."""

    queryset_class = StockReservationQuerySet

    def for_list(self) -> StockReservationQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> StockReservationQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()
