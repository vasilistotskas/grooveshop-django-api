from decimal import Decimal

from django.db import models
from django.db.models import QuerySet

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from order.enum.status_enum import StatusEnum
from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum


class Order(TimeStampMixinModel, UUIDModel):
    id = models.AutoField(primary_key=True)
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
    email = models.CharField(max_length=100)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    street = models.CharField(max_length=100)
    street_number = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    zipcode = models.CharField(max_length=100)
    place = models.CharField(max_length=100, blank=True, null=True)
    phone = models.CharField(max_length=100)
    mobile_phone = models.CharField(max_length=100, null=True, blank=True, default=None)
    paid_amount = models.DecimalField(max_digits=8, decimal_places=2)
    customer_notes = models.TextField(blank=True, null=True)
    status = models.CharField(
        max_length=20, choices=StatusEnum.choices(), default=StatusEnum.PENDING.value
    )
    shipping_price = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    document_type = models.CharField(
        max_length=10,
        choices=[("receipt", "Receipt"), ("invoice", "Invoice")],
        default="receipt",
    )

    class Meta:
        ordering = [
            "-created_at",
        ]

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
    id = models.AutoField(primary_key=True)
    order = models.ForeignKey(
        "order.Order", related_name="order_item_order", on_delete=models.CASCADE
    )
    product = models.ForeignKey(
        "product.Product", related_name="order_item_product", on_delete=models.CASCADE
    )
    price = models.DecimalField(max_digits=8, decimal_places=2)
    quantity = models.IntegerField(default=1)

    def __str__(self):
        return "%s" % self.id

    @property
    def total_price(self) -> Decimal:
        return self.price * self.quantity

    def get_ordering_queryset(self) -> QuerySet:
        return self.order.order_item_order.all()
