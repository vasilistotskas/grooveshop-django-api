from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from djmoney.money import Money

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

    @property
    def price(self) -> Money:
        return Money(self.product.price.amount, settings.DEFAULT_CURRENCY)

    @property
    def final_price(self) -> Money:
        return Money(self.product.final_price.amount, settings.DEFAULT_CURRENCY)

    @property
    def discount_value(self) -> Money:
        return Money(
            self.product.discount_value.amount, settings.DEFAULT_CURRENCY
        )

    @property
    def price_save_percent(self) -> Decimal:
        return self.product.price_save_percent

    @property
    def discount_percent(self) -> Decimal:
        return self.product.discount_percent

    @property
    def vat_percent(self) -> Decimal:
        return self.product.vat_percent

    @property
    def vat_value(self) -> Money:
        return self.product.vat_value

    @property
    def total_price(self) -> Money:
        return Money(
            self.quantity * self.product.final_price.amount,
            settings.DEFAULT_CURRENCY,
        )

    @property
    def total_discount_value(self) -> Money:
        return Money(
            self.quantity * self.product.discount_value.amount,
            settings.DEFAULT_CURRENCY,
        )

    def update_quantity(self, quantity: int):
        self.quantity = quantity
        self.save()
