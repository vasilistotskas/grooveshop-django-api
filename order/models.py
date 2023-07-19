from decimal import Decimal

from django.db import models
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from order.enum.status_enum import StatusEnum
from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum


class Order(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="order_user",
        on_delete=models.CASCADE,
        null=True,
    )
    pay_way = models.ForeignKey(
        "pay_way.PayWay",
        related_name="order_pay_way",
        on_delete=models.SET_NULL,
        null=True,
    )
    country = models.ForeignKey(
        "country.Country",
        related_name="order_country",
        on_delete=models.CASCADE,
    )
    region = models.ForeignKey(
        "region.Region",
        related_name="order_region",
        on_delete=models.CASCADE,
    )
    floor = models.CharField(
        max_length=50,
        choices=FloorChoicesEnum.choices(),
        null=True,
        blank=True,
        default=None,
    )
    location_type = models.CharField(
        max_length=100,
        choices=LocationChoicesEnum.choices(),
        null=True,
        blank=True,
        default=None,
    )
    email = models.CharField(_("Email"), max_length=255)
    first_name = models.CharField(_("First Name"), max_length=255)
    last_name = models.CharField(_("Last Name"), max_length=255)
    street = models.CharField(_("Street"), max_length=255)
    street_number = models.CharField(_("Street Number"), max_length=255)
    city = models.CharField(_("City"), max_length=255)
    zipcode = models.CharField(_("Zipcode"), max_length=255)
    place = models.CharField(_("Place"), max_length=255, blank=True, null=True)
    phone = models.CharField(_("Phone"), max_length=255)
    mobile_phone = models.CharField(
        _("Mobile Phone"), max_length=255, null=True, blank=True, default=None
    )
    paid_amount = models.DecimalField(_("Paid Amount"), max_digits=8, decimal_places=2)
    customer_notes = models.TextField(_("Customer Notes"), null=True, blank=True)
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=StatusEnum.choices(),
        default=StatusEnum.PENDING.value,
    )
    shipping_price = models.DecimalField(
        _("Shipping Price"), max_digits=8, decimal_places=2, default=0
    )
    document_type = models.CharField(
        _("Document Type"),
        max_length=10,
        choices=[("receipt", _("Receipt")), ("invoice", _("Invoice"))],
        default="receipt",
    )

    class Meta:
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")
        ordering = ["-created_at"]

    def __str__(self):
        return self.first_name

    @property
    def total_price(self) -> Decimal:
        return (
            sum(item.total_price for item in self.order_item_order.all())
            + self.shipping_price
        )

    @property
    def full_address(self) -> str:
        return f"{self.street} {self.street_number}, {self.zipcode} {self.city}"


class OrderItem(TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    order = models.ForeignKey(
        "order.Order", related_name="order_item_order", on_delete=models.CASCADE
    )
    product = models.ForeignKey(
        "product.Product", related_name="order_item_product", on_delete=models.CASCADE
    )
    price = models.DecimalField(_("Price"), max_digits=8, decimal_places=2)
    quantity = models.IntegerField(_("Quantity"), default=1)

    class Meta:
        verbose_name = _("Order Item")
        verbose_name_plural = _("Order Items")
        ordering = ["sort_order"]

    def __str__(self):
        return "%s" % self.id

    @property
    def total_price(self) -> Decimal:
        return self.price * self.quantity

    def get_ordering_queryset(self) -> QuerySet:
        return self.order.order_item_order.all()
