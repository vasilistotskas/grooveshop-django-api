from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Count, ExpressionWrapper, F, Prefetch, Sum
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

    def _enriched_items_prefetch(self) -> Prefetch:
        """Prefetch ``items`` with each item's product enriched for the
        embedded ``ProductSerializer`` — translations, category/vat/brand,
        review/likes counts, main image and attributes — so serializing the
        order's line items stays O(1) queries (G0226).

        Both the list (``OrderSerializer``) and detail
        (``OrderDetailSerializer``) responses embed
        ``OrderItemDetailSerializer`` → the full ``ProductSerializer``, so
        both tiers need the same enrichment. The Product optimizers are
        composed directly rather than via ``Product.objects.for_list()`` to
        avoid its active-only filter — an order legitimately references a
        product that may later be deactivated.
        """
        from order.models.item import OrderItem
        from product.models.product import Product

        product_qs = (
            Product.objects.with_translations()
            .with_category()
            .with_counts()
            .with_main_image()
            .with_product_attributes()
        )
        return Prefetch(
            "items",
            queryset=OrderItem.objects.prefetch_related(
                Prefetch("product", queryset=product_qs)
            ),
        )

    def with_items(self) -> Self:
        """Prefetch order items with fully enriched product data."""
        return self.prefetch_related(self._enriched_items_prefetch())

    def with_items_basic(self) -> Self:
        """Prefetch order items for list views (same enrichment as
        ``with_items`` — both tiers embed the full product serializer)."""
        return self.prefetch_related(self._enriched_items_prefetch())

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

        Includes user, payment info, counts, and pre-aggregated item totals
        so that Order.total_price_items avoids extra DB queries.
        """
        return (
            self.exclude_deleted()
            .with_user()
            .with_payment_info()
            .with_items_basic()
            .with_counts()
            .with_total_amounts()
        )

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Includes everything from for_list() plus full item details and
        an eager-load of the linked shipping_provider row + provider
        shipment objects so the Order detail serializer never N+1s on
        ``boxnow_shipment`` / ``acs_shipment``.
        """
        from order.models.history import OrderHistory

        return (
            self.exclude_deleted()
            .with_user()
            .with_payment_info()
            .with_items()
            .with_counts()
            .select_related("shipping_provider")
            .prefetch_related(
                Prefetch(
                    "history",
                    queryset=OrderHistory.objects.select_related(
                        "user"
                    ).order_by("created_at"),
                ),
                "boxnow_shipment",
                "acs_shipment",
                "acs_shipment__events",
                "acs_shipment__station_destination",
                "invoice",
            )
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

    def delete(self) -> tuple[int, dict[str, int]]:
        """Bulk SOFT-delete, mirroring the per-instance
        ``SoftDeleteModel.delete()`` contract: ``Order.objects.filter(...)
        .delete()`` must mark rows deleted, not hard-delete them (G0246).
        Use ``hard_delete()`` for a real DELETE."""
        from django.utils import timezone

        count = super().update(is_deleted=True, deleted_at=timezone.now())
        return count, {self.model._meta.label: count}

    def hard_delete(self) -> tuple[int, dict[str, int]]:
        """Permanently DELETE the rows, bypassing soft-delete."""
        return super().delete()

    def restore(self) -> int:
        """Un-delete previously soft-deleted rows."""
        return super().update(is_deleted=False, deleted_at=None)


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

    def all_with_deleted(self) -> OrderQuerySet:
        """Base queryset INCLUDING soft-deleted orders — the default
        ``get_queryset`` applies ``exclude_deleted()``, so deleted rows are
        otherwise unreachable for admin / audit / reconciliation (G0246)."""
        return OrderQuerySet(self.model, using=self._db)

    def deleted_only(self) -> OrderQuerySet:
        """Only the soft-deleted orders."""
        return OrderQuerySet(self.model, using=self._db).deleted_only()
