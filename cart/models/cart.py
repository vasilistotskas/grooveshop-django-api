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
        return self.items.prefetch_related("product").all()

    @property
    def total_price(self) -> Money:
        total = sum(item.total_price.amount for item in self.get_items())
        return Money(total, settings.DEFAULT_CURRENCY)

    @property
    def total_discount_value(self) -> Money:
        total = sum(
            item.total_discount_value.amount for item in self.get_items()
        )
        return Money(total, settings.DEFAULT_CURRENCY)

    @property
    def total_vat_value(self) -> Money:
        total = sum(item.vat_value.amount for item in self.get_items())
        return Money(total, settings.DEFAULT_CURRENCY)

    @property
    def total_items(self) -> int | Literal[0]:
        return sum(item.quantity for item in self.get_items())

    @property
    def total_items_unique(self) -> int:
        return self.items.count()
