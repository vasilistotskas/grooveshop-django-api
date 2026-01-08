from typing import Literal

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.money import Money

from cart.managers.cart import CartManager
from core.models import TimeStampMixinModel, UUIDModel


class Cart(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="cart",
        null=True,
        blank=True,
        default=None,
        on_delete=models.CASCADE,
    )
    last_activity = models.DateTimeField(_("Last Activity"), auto_now=True)

    objects: CartManager = CartManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Cart")
        verbose_name_plural = _("Carts")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["user"], name="cart_user_ix"),
            BTreeIndex(fields=["last_activity"], name="cart_last_activity_ix"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["user"], name="unique_user_cart"),
        ]

    def __str__(self):
        if self.user:
            return f"Cart for {self.user} - Items: {self.total_items} - Total: {self.total_price}"
        else:
            return f"Guest Cart {self.id} - Items: {self.total_items} - Total: {self.total_price}"

    def refresh_last_activity(self):
        self.last_activity = now()
        self.save()

    def get_items(self):
        """Get cart items with optimized prefetching to avoid N+1 queries."""
        # If items are already prefetched, use them directly
        if "items" in getattr(self, "_prefetched_objects_cache", {}):
            return self.items.all()
        # Otherwise, fetch with optimized prefetching
        return (
            self.items.select_related("product__category", "product__vat")
            .prefetch_related("product__translations")
            .all()
        )

    @property
    def total_price(self) -> Money:
        # Use prefetched items if available
        items = (
            self.items.all()
            if "items" in getattr(self, "_prefetched_objects_cache", {})
            else self.get_items()
        )
        total = sum(item.total_price.amount for item in items)
        return Money(total, settings.DEFAULT_CURRENCY)

    @property
    def total_discount_value(self) -> Money:
        # Use prefetched items if available
        items = (
            self.items.all()
            if "items" in getattr(self, "_prefetched_objects_cache", {})
            else self.get_items()
        )
        total = sum(item.total_discount_value.amount for item in items)
        return Money(total, settings.DEFAULT_CURRENCY)

    @property
    def total_vat_value(self) -> Money:
        # Use prefetched items if available
        items = (
            self.items.all()
            if "items" in getattr(self, "_prefetched_objects_cache", {})
            else self.get_items()
        )
        total = sum(item.vat_value.amount for item in items)
        return Money(total, settings.DEFAULT_CURRENCY)

    @property
    def total_items(self) -> int | Literal[0]:
        """
        Return the total quantity of all items in the cart.

        Uses annotated value if available (from optimized queryset),
        otherwise calculates from items.
        """
        if hasattr(self, "_total_quantity"):
            return self._total_quantity or 0
        # Use prefetched items if available
        items = (
            self.items.all()
            if "items" in getattr(self, "_prefetched_objects_cache", {})
            else self.get_items()
        )
        return sum(item.quantity for item in items)

    @property
    def total_items_unique(self) -> int:
        """
        Return the number of unique items in the cart.

        Uses annotated value if available (from optimized queryset),
        otherwise queries the database.
        """
        if hasattr(self, "_items_count"):
            return self._items_count or 0
        return self.items.count()
