from decimal import Decimal
from typing import Literal, cast

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.money import Money

from cart.managers.cart import CartManager
from core.models import TimeStampMixinModel, UUIDModel
from order.discounts import allocate_discount, vat_buckets


class Cart(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="cart",
        null=True,
        blank=True,
        default=None,
        on_delete=models.CASCADE,
        verbose_name=_("User"),
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
        count = self.total_items
        if self.user:
            return ngettext(
                "Cart for %(user)s - %(count)s item - Total: %(total)s",
                "Cart for %(user)s - %(count)s items - Total: %(total)s",
                count,
            ) % {"user": self.user, "count": count, "total": self.total_price}
        return ngettext(
            "Guest cart %(id)s - %(count)s item - Total: %(total)s",
            "Guest cart %(id)s - %(count)s items - Total: %(total)s",
            count,
        ) % {"id": self.id, "count": count, "total": self.total_price}

    def refresh_last_activity(self):
        # Use UPDATE to touch only last_activity — avoids triggering
        # pre_save/post_save signals and saves all other fields.
        Cart.objects.filter(pk=self.pk).update(last_activity=now())
        self.last_activity = now()

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
        """VAT contained in ``total_price``, before any promotion."""
        return self.vat_after_discount(Money(0, settings.DEFAULT_CURRENCY))

    def vat_after_discount(self, discount: Money) -> Money:
        """VAT owed once ``discount`` is taken off the line prices.

        Line prices are what the shopper pays (markdown included), so VAT
        is backed out of them; a price discount granted at the time of
        sale reduces the taxable base, so it is spread across the lines
        (and therefore across their VAT rates) exactly as the invoice
        does it — see ``order.discounts``. Never pass a gift card here:
        it is a means of payment and leaves VAT untouched.
        """
        # Use prefetched items if available
        items = list(
            self.items.all()
            if "items" in getattr(self, "_prefetched_objects_cache", {})
            else self.get_items()
        )
        gross = {item.pk: item.total_price.amount for item in items}
        discounted = allocate_discount(gross, Decimal(discount.amount))
        buckets = vat_buckets(
            (discounted[item.pk], Decimal(item.vat_percent)) for item in items
        )
        total = sum((row["vat"] for row in buckets.values()), Decimal(0))
        return Money(total, settings.DEFAULT_CURRENCY)

    @property
    def total_items(self) -> int | Literal[0]:
        """
        Return the total quantity of all items in the cart.

        Uses annotated value if available (from optimized queryset),
        otherwise calculates from items.
        """
        if hasattr(self, "_total_quantity"):
            return cast(int, self._total_quantity) or 0
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
            return cast(int, self._items_count) or 0
        return self.items.count()

    @property
    def total_weight_grams(self) -> int:
        """Total cart weight in grams across all line items.

        Used by the checkout shipping-quote call so ACS live pricing
        can quote against the actual weight bracket. Wraps the
        canonical ``shipping.utils.compute_total_weight_grams`` so a
        carrier registry change propagates here automatically.
        """
        from shipping.utils import compute_total_weight_grams

        items = (
            self.items.all()
            if "items" in getattr(self, "_prefetched_objects_cache", {})
            else self.get_items()
        )
        return compute_total_weight_grams(
            (item.product, item.quantity) for item in items
        )
