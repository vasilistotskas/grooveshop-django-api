from decimal import Decimal

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
        "order.Order", related_name="order_item_order", on_delete=models.CASCADE
    )
    product = models.ForeignKey(
        "product.Product", related_name="order_item_product", on_delete=models.CASCADE
    )
    price = MoneyField(_("Price"), max_digits=19, decimal_places=4)
    quantity = models.IntegerField(_("Quantity"), default=1)

    class Meta(TypedModelMeta):
        verbose_name = _("Order Item")
        verbose_name_plural = _("Order Items")
        ordering = ["sort_order"]

    def __str__(self):
        return "%s" % self.id

    @property
    def total_price(self) -> Money:
        price = self.price.amount * self.quantity
        return Money(amount=Decimal(price), currency=self.price.currency)

    def get_ordering_queryset(self) -> QuerySet:
        return self.order.order_item_order.all()
