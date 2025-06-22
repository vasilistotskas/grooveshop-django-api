from decimal import Decimal

from django.contrib.postgres.indexes import BTreeIndex
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from djmoney.money import Money

from core.models import SortableModel, TimeStampMixinModel, UUIDModel
from order.managers.item import OrderItemManager


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
    original_quantity = models.IntegerField(
        _("Original Quantity"), null=True, blank=True
    )
    is_refunded = models.BooleanField(_("Is Refunded"), default=False)
    refunded_quantity = models.IntegerField(_("Refunded Quantity"), default=0)
    notes = models.TextField(_("Notes"), blank=True, default="")

    objects: OrderItemManager = OrderItemManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Order Item")
        verbose_name_plural = _("Order Items")
        ordering = ["sort_order"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(fields=["product"], name="order_item_product_ix"),
            BTreeIndex(
                fields=["is_refunded"], name="order_item_is_refunded_ix"
            ),
        ]

    def __str__(self):
        product_name = self.product.safe_translation_getter(
            "name", any_language=True
        )
        return f"Order {self.order.id} - {product_name} x {self.quantity}"

    def clean(self):
        if self.quantity <= 0:
            raise ValidationError(_("Quantity must be greater than 0."))

        if (
            hasattr(self.product, "stock")
            and self.quantity > self.product.stock
            and not self.pk
        ):
            raise ValidationError(
                _("The quantity exceeds the available stock.")
            )

        if self.refunded_quantity > self.quantity:
            raise ValidationError(
                _("Refunded quantity cannot exceed the ordered quantity.")
            )

    @property
    def total_price(self) -> Money:
        return Money(
            amount=self.price.amount * Decimal(self.quantity),
            currency=self.price.currency,
        )

    @property
    def refunded_amount(self) -> Money:
        if self.refunded_quantity == 0:
            return Money(amount=0, currency=self.price.currency)

        return Money(
            amount=self.price.amount * Decimal(self.refunded_quantity),
            currency=self.price.currency,
        )

    @property
    def net_quantity(self) -> int:
        return self.quantity - self.refunded_quantity

    @property
    def net_price(self) -> Money:
        return Money(
            amount=self.price.amount * Decimal(self.net_quantity),
            currency=self.price.currency,
        )

    def save(self, *args, **kwargs):
        if not self.pk and self.original_quantity is None:
            self.original_quantity = self.quantity
        super().save(*args, **kwargs)

    def refund(self, quantity=None):
        refund_qty = quantity if quantity is not None else self.quantity

        if refund_qty <= 0:
            raise ValidationError(
                _(
                    "Invalid refund quantity. Please enter a quantity greater than 0."
                )
            )

        if self.refunded_quantity + refund_qty > self.quantity:
            raise ValidationError(
                _(
                    "Cannot refund more than the ordered quantity. "
                    f"Ordered quantity: {self.quantity}, "
                    f"Refunded quantity: {self.refunded_quantity}, "
                    f"Refund quantity: {refund_qty}"
                )
            )

        if hasattr(self.product, "stock"):
            self.product.stock += refund_qty
            self.product.save(update_fields=["stock"])

        self.refunded_quantity += refund_qty
        if self.refunded_quantity == self.quantity:
            self.is_refunded = True

        self.save(update_fields=["refunded_quantity", "is_refunded"])

        return Money(
            amount=self.price.amount * Decimal(refund_qty),
            currency=self.price.currency,
        )

    def get_ordering_queryset(self):
        return self.order.items.all()
