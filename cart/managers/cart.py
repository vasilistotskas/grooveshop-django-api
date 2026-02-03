from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.db import models
from django.utils import timezone
from extra_settings.models import Setting

from core.managers import OptimizedManager, OptimizedQuerySet

if TYPE_CHECKING:
    from typing import Self


class CartQuerySet(OptimizedQuerySet):
    """
    Optimized QuerySet for Cart model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def active(self) -> Self:
        abandoned_threshold = Setting.get("CART_ABANDONED_HOURS", default=24)
        cutoff_time = timezone.now() - timedelta(hours=abandoned_threshold)
        return self.filter(last_activity__gte=cutoff_time).exclude(
            items__isnull=True
        )

    def abandoned(self) -> Self:
        abandoned_threshold = Setting.get("CART_ABANDONED_HOURS", default=24)
        cutoff_time = timezone.now() - timedelta(hours=abandoned_threshold)
        return self.filter(last_activity__lt=cutoff_time)

    def empty(self) -> Self:
        return self.filter(items__isnull=True)

    def with_items(self) -> Self:
        return self.filter(items__isnull=False)

    def for_user(self, user) -> Self:
        if user.is_authenticated:
            return self.filter(user=user)
        return self.none()

    def guest_carts(self) -> Self:
        return self.filter(user__isnull=True)

    def user_carts(self) -> Self:
        return self.filter(user__isnull=False)

    def with_user(self) -> Self:
        """Select related user."""
        return self.select_related("user")

    def with_items_prefetch(self) -> Self:
        """Prefetch cart items with product data."""
        return self.prefetch_related(
            "items",
            "items__product",
            "items__product__translations",
            "items__product__category",
            "items__product__vat",
        )

    def with_totals(self) -> Self:
        """Annotate with total quantity and items count."""
        return self.annotate(
            _total_quantity=models.Sum("items__quantity"),
            _items_count=models.Count("items", distinct=True),
        )

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes user, items with product data, and totals.
        """
        return self.with_user().with_items_prefetch().with_totals()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Includes everything from for_list() plus full item details.
        """
        return self.with_user().with_items_prefetch().with_totals()

    def expired(self, days=30) -> Self:
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.filter(last_activity__lt=cutoff_date)

    def by_date_range(self, start_date, end_date) -> Self:
        return self.filter(created_at__date__range=[start_date, end_date])

    def recent(self, days=7) -> Self:
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=cutoff_date)

    def by_country(self, country_code) -> Self:
        return self.filter(user__country__alpha_2=country_code)

    def with_specific_product(self, product) -> Self:
        return self.filter(items__product=product).distinct()


class CartManager(OptimizedManager):
    """
    Manager for Cart model with optimized queryset methods.

    Most methods are automatically delegated to CartQuerySet via __getattr__.
    Only methods with custom logic beyond simple delegation are explicitly defined.

    Usage in ViewSet:
        def get_queryset(self):
            return Cart.objects.for_detail()
    """

    queryset_class = CartQuerySet

    def get_queryset(self) -> CartQuerySet:
        return CartQuerySet(self.model, using=self._db)

    def for_list(self) -> CartQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> CartQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()

    def cleanup_expired(self, days=30) -> int:
        """
        Delete expired carts and return count.

        This method has custom logic beyond simple delegation,
        so it's explicitly defined on the Manager.
        """
        expired_carts = self.expired(days)
        count = expired_carts.count()
        expired_carts.delete()
        return count
