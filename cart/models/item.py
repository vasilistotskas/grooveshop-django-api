from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from djmoney.money import Money
from djmoney.models.fields import MoneyField

from cart.managers.item import CartItemManager
from core.models import TimeStampMixinModel, UUIDModel


class CartItem(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    cart = models.ForeignKey(
        "cart.Cart", related_name="items", on_delete=models.CASCADE
    )
    product = models.ForeignKey(
        "product.Product", related_name="cart_items", on_delete=models.CASCADE
    )
    quantity = models.PositiveIntegerField(_("Quantity"), default=1)
    price_at_add = MoneyField(
        _("Price at Add"),
        max_digits=10,
        decimal_places=2,
        default_currency=settings.DEFAULT_CURRENCY,
        null=True,
        blank=True,
        help_text=_(
            "Price of the product when added to cart (for price change validation)"
        ),
    )

    objects: CartItemManager = CartItemManager()

    class Meta:
        verbose_name = _("Cart Item")
        verbose_name_plural = _("Cart Items")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["cart"], name="cart_item_cart_ix"),
            BTreeIndex(fields=["product"], name="cart_item_product_ix"),
            BTreeIndex(fields=["quantity"], name="cart_item_quantity_ix"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "product"], name="unique_cart_item"
            )
        ]

    def __str__(self):
        return (
            f"CartItem {self.id} in Cart"
            f" {self.cart.id}: {self.product.safe_translation_getter('name', any_language=True)}"
            f" x {self.quantity}"
        )

    def _b2b_resolved(self):
        """The bound wholesale price for this line, or None (retail).

        The pricing context rides on the cart INSTANCE (see
        ``b2b.services.B2BPricingService.bind_cart``). Items reached
        through ``cart.items`` querysets share that instance via
        Django's known-related-objects cache; any other path falls back
        to retail through this ``getattr`` — safely, never wrong-priced.

        ``price_for`` resolves lazily on a bulk-map miss (a product
        added after the bind — e.g. the very add-to-cart request whose
        ``price_at_add`` snapshot this feeds), so a BOUND cart can
        never price a line at retail.
        """
        context = getattr(self.cart, "_b2b_pricing", None)
        if context is None:
            return None
        return context.price_for(self.product)

    @property
    def price(self) -> Money:
        resolved = self._b2b_resolved()
        if resolved is not None:
            return resolved.net
        return Money(self.product.price.amount, settings.DEFAULT_CURRENCY)

    @property
    def final_price(self) -> Money:
        resolved = self._b2b_resolved()
        if resolved is not None:
            return resolved.final
        return Money(self.product.final_price.amount, settings.DEFAULT_CURRENCY)

    @property
    def discount_value(self) -> Money:
        # B2B: NET savings vs the retail net price — the same basis the
        # retail branch uses (product.discount_value is net-based).
        resolved = self._b2b_resolved()
        if resolved is not None:
            saving = self.product.price.amount - resolved.net.amount
            return Money(max(saving, 0), settings.DEFAULT_CURRENCY)
        return Money(
            self.product.discount_value.amount, settings.DEFAULT_CURRENCY
        )

    @property
    def price_save_percent(self) -> Decimal:
        resolved = self._b2b_resolved()
        if resolved is not None:
            return self._b2b_net_percent(resolved)
        return self.product.price_save_percent

    @property
    def discount_percent(self) -> Decimal:
        resolved = self._b2b_resolved()
        if resolved is not None:
            return self._b2b_net_percent(resolved)
        return self.product.discount_percent

    def _b2b_net_percent(self, resolved) -> Decimal:
        """Effective percent off the retail NET price."""
        retail_net = self.product.price.amount
        if retail_net <= 0:
            return Decimal(0)
        return (retail_net - resolved.net.amount) / retail_net * 100

    @property
    def vat_percent(self) -> Decimal:
        return self.product.vat_percent

    @property
    def vat_value(self) -> Money:
        resolved = self._b2b_resolved()
        if resolved is not None:
            return resolved.final - resolved.net
        return self.product.vat_value

    @property
    def total_price(self) -> Money:
        return Money(
            self.quantity * self.final_price.amount,
            settings.DEFAULT_CURRENCY,
        )

    @property
    def total_discount_value(self) -> Money:
        return Money(
            self.quantity * self.discount_value.amount,
            settings.DEFAULT_CURRENCY,
        )

    @property
    def total_vat_value(self) -> Money:
        return Money(
            self.quantity * self.vat_value.amount,
            settings.DEFAULT_CURRENCY,
        )

    def update_quantity(self, quantity: int):
        self.quantity = quantity
        self.save()

    def save(self, *args, **kwargs):
        """Override save to set price_at_add for new items."""
        # Set price_at_add when creating a new cart item. Bound-aware:
        # a wholesale buyer's snapshot records the price they actually
        # saw, so drift logging stays meaningful for B2B carts.
        if self.pk is None and self.price_at_add is None:
            self.price_at_add = self.final_price
        super().save(*args, **kwargs)
