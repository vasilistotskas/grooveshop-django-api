from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.money import Money

from core.models import TimeStampMixinModel
from core.models import UUIDModel


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

    class Meta(TypedModelMeta):
        verbose_name = _("Cart")
        verbose_name_plural = _("Carts")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user"], name="unique_user_cart"),
        ]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["user"]),
        ]

    def __unicode__(self):
        return f"Cart {self.user} - Items: {self.total_items} - Total: {self.total_price}"

    def __str__(self):
        return f"Cart {self.user} - Items: {self.total_items} - Total: {self.total_price}"

    def get_items(self) -> models.QuerySet:
        return self.items.prefetch_related("product").all()

    def refresh_last_activity(self):
        self.last_activity = now()
        self.save()

    @property
    def total_price(self) -> Money:
        price = sum([item.total_price.amount for item in self.get_items()])
        return Money(price, settings.DEFAULT_CURRENCY)

    @property
    def total_discount_value(self) -> Money:
        value = sum([item.total_discount_value.amount for item in self.get_items()])
        return Money(value, settings.DEFAULT_CURRENCY)

    @property
    def total_vat_value(self) -> Money:
        value = sum([item.product.vat_value.amount for item in self.get_items()])
        return Money(value, settings.DEFAULT_CURRENCY)

    @property
    def total_items(self) -> int:
        return sum([item.quantity for item in self.get_items()])

    @property
    def total_items_unique(self) -> int:
        return self.items.count()


class CartItem(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    cart = models.ForeignKey("cart.Cart", related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey("product.Product", related_name="cart_items", on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(_("Quantity"), default=1)

    class Meta(TypedModelMeta):
        verbose_name = _("Cart Item")
        verbose_name_plural = _("Cart Items")
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["cart", "product"], name="unique_cart_item")]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["cart", "product"]),
        ]

    def __unicode__(self):
        product_name = self.product.safe_translation_getter("name", any_language=True)
        return f"CartItem {self.id} in Cart {self.cart.id}: {product_name} x {self.quantity}"

    def __str__(self):
        product_name = self.product.safe_translation_getter("name", any_language=True)
        return f"CartItem {self.id} in Cart {self.cart.id}: {product_name} x {self.quantity}"

    @property
    def price(self) -> Money:
        price = self.product.price.amount
        return Money(price, settings.DEFAULT_CURRENCY)

    @property
    def final_price(self) -> Money:
        price = self.product.final_price.amount
        return Money(price, settings.DEFAULT_CURRENCY)

    @property
    def discount_value(self) -> Money:
        value = self.product.discount_value.amount
        return Money(value, settings.DEFAULT_CURRENCY)

    @property
    def price_save_percent(self) -> Decimal:
        return self.product.price_save_percent

    @property
    def discount_percent(self) -> Decimal:
        return self.product.discount_percent

    @property
    def vat_percent(self) -> Decimal | int:
        return self.product.vat_percent

    @property
    def vat_value(self) -> Money:
        return self.product.vat_value

    @property
    def total_price(self) -> Money:
        price = self.quantity * self.product.final_price.amount
        return Money(price, settings.DEFAULT_CURRENCY)

    @property
    def total_discount_value(self) -> Money:
        value = self.quantity * self.product.discount_value.amount
        return Money(value, settings.DEFAULT_CURRENCY)

    def update_quantity(self, quantity) -> None:
        self.quantity = quantity
        self.save()
