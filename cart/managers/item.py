from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.db import models
from django.utils import timezone
from extra_settings.models import Setting

from core.managers import OptimizedManager, OptimizedQuerySet

if TYPE_CHECKING:
    from typing import Self


class CartItemQuerySet(OptimizedQuerySet):
    """
    Optimized QuerySet for CartItem model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_product(self) -> Self:
        """Select related product."""
        return self.select_related("product")

    def with_product_translations(self) -> Self:
        """Prefetch product translations."""
        return self.prefetch_related("product__translations")

    def with_product_images(self) -> Self:
        """Prefetch product images."""
        return self.prefetch_related("product__images__translations")

    def with_cart(self) -> Self:
        """Select related cart."""
        return self.select_related("cart")

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes product with translations and cart.
        """
        return self.with_product().with_product_translations().with_cart()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Includes everything from for_list() plus product images.
        """
        return self.for_list().with_product_images()

    def for_cart(self, cart):
        return self.filter(cart=cart)

    def for_product(self, product):
        return self.filter(product=product)

    def for_user(self, user):
        if user.is_authenticated:
            return self.filter(cart__user=user)
        return self.none()

    def total_quantity(self):
        return self.aggregate(total=models.Sum("quantity"))["total"] or 0

    def by_product_popularity(self):
        return (
            self.values("product")
            .annotate(
                total_quantity=models.Sum("quantity"),
                cart_count=models.Count("cart", distinct=True),
            )
            .order_by("-total_quantity", "-cart_count")
        )

    def high_quantity(self, threshold=5):
        return self.filter(quantity__gte=threshold)

    def recent(self, days=7):
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=cutoff_date)

    def by_date_range(self, start_date, end_date):
        return self.filter(created_at__date__range=[start_date, end_date])

    def by_quantity_range(self, min_qty, max_qty):
        return self.filter(quantity__range=[min_qty, max_qty])

    def by_price_range(self, min_price, max_price):
        """Filter items by product price range."""
        return self.filter(
            product__price__gte=min_price,
            product__price__lte=max_price,
        )

    def expensive_items(self, threshold=100):
        """Filter items with product price above threshold."""
        return self.filter(product__price__gte=threshold)

    def in_active_carts(self):
        abandoned_threshold = Setting.get("CART_ABANDONED_HOURS", default=24)
        cutoff_time = timezone.now() - timedelta(hours=abandoned_threshold)
        return self.filter(cart__last_activity__gte=cutoff_time)

    def in_abandoned_carts(self):
        abandoned_threshold = Setting.get("CART_ABANDONED_HOURS", default=24)
        cutoff_time = timezone.now() - timedelta(hours=abandoned_threshold)
        return self.filter(cart__last_activity__lt=cutoff_time)

    def with_discounts(self):
        return self.filter(product__discount_percent__gt=0)


class CartItemManager(OptimizedManager):
    """
    Manager for CartItem model with optimized queryset methods.

    Most methods are automatically delegated to CartItemQuerySet via __getattr__.
    Only get_queryset(), for_list(), and for_detail() are explicitly defined.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return CartItem.objects.for_list()
            return CartItem.objects.for_detail()
    """

    queryset_class = CartItemQuerySet

    def get_queryset(self) -> CartItemQuerySet:
        return CartItemQuerySet(self.model, using=self._db)

    def for_list(self) -> CartItemQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> CartItemQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()
