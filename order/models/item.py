from decimal import Decimal
from typing import override

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from djmoney.money import Money

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel


class OrderItem(TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    order = models.ForeignKey(
        "order.Order",
        related_name="items",
        on_delete=models.CASCADE,
    )
    product = models.ForeignKey(
        "product.Product",
        related_name="order_items",
        on_delete=models.CASCADE,
    )
    price = MoneyField(_("Price"), max_digits=11, decimal_places=2)
    quantity = models.IntegerField(_("Quantity"), default=1)

    class Meta(TypedModelMeta):
        verbose_name = _("Order Item")
        verbose_name_plural = _("Order Items")
        ordering = ["sort_order"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
        ]

    def __unicode__(self):
        product_name = self.product.safe_translation_getter("name", any_language=True)
        return "Order %s - %s x %s" % (
            self.order.id,
            product_name,
            self.quantity,
        )

    def __str__(self):
        product_name = self.product.safe_translation_getter("name", any_language=True)
        return f"Order {self.order.id} - {product_name} x {self.quantity}"

    @override
    def clean(self):
        if self.quantity <= 0:
            raise ValidationError(_("Quantity must be greater than 0."))

        if hasattr(self.product, "stock") and self.quantity > self.product.stock:
            raise ValidationError(_("The quantity exceeds the available stock."))

    @property
    def total_price(self) -> Money:
        return Money(
            amount=self.price.amount * Decimal(self.quantity),
            currency=self.price.currency,
        )

    @override
    def get_ordering_queryset(self) -> QuerySet:
        return self.order.items.all()
