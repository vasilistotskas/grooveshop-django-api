from typing import override

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models
from django.db.models import ExpressionWrapper
from django.db.models import F
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from djmoney.money import Money
from phonenumber_field.modelfields import PhoneNumberField

from core.models import SoftDeleteModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from order.enum.document_type_enum import OrderDocumentTypeEnum
from order.enum.status_enum import OrderStatusEnum
from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum


class OrderManager(models.Manager):
    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("user", "pay_way", "country", "region")
            .exclude(is_deleted=True)
        )


class Order(SoftDeleteModel, TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="orders",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    pay_way = models.ForeignKey(
        "pay_way.PayWay",
        related_name="orders",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    country = models.ForeignKey(
        "country.Country",
        related_name="orders",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    region = models.ForeignKey(
        "region.Region",
        related_name="orders",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    floor = models.CharField(
        max_length=50,
        choices=FloorChoicesEnum,
        null=True,
        blank=True,
        default=None,
    )
    location_type = models.CharField(
        max_length=100,
        choices=LocationChoicesEnum,
        null=True,
        blank=True,
        default=None,
    )
    email = models.EmailField(_("Email"), max_length=255)
    first_name = models.CharField(_("First Name"), max_length=255)
    last_name = models.CharField(_("Last Name"), max_length=255)
    street = models.CharField(_("Street"), max_length=255)
    street_number = models.CharField(_("Street Number"), max_length=255)
    city = models.CharField(_("City"), max_length=255)
    zipcode = models.CharField(_("Zipcode"), max_length=255)
    place = models.CharField(_("Place"), max_length=255, blank=True, null=True)
    phone = PhoneNumberField(_("Phone Number"))
    mobile_phone = PhoneNumberField(_("Mobile Phone Number"), null=True, blank=True, default=None)
    customer_notes = models.TextField(_("Customer Notes"), null=True, blank=True)
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=OrderStatusEnum,
        default=OrderStatusEnum.PENDING,
    )
    shipping_price = MoneyField(_("Shipping Price"), max_digits=11, decimal_places=2, default=0)
    document_type = models.CharField(
        _("Document Type"),
        max_length=100,
        choices=OrderDocumentTypeEnum,
        default=OrderDocumentTypeEnum.RECEIPT,
    )
    paid_amount = MoneyField(
        _("Paid Amount"),
        max_digits=11,
        decimal_places=2,
        null=True,
        default=0,
    )

    objects = OrderManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["status"]),
            BTreeIndex(fields=["document_type"]),
            GinIndex(
                name="order_search_gin",
                fields=[
                    "first_name",
                    "last_name",
                    "email",
                    "phone",
                    "mobile_phone",
                    "street",
                    "city",
                    "zipcode",
                    "place",
                ],
                opclasses=["gin_trgm_ops"] * 9,
            ),
        ]

    def __unicode__(self):
        return f"Order {self.id} - {self.first_name} {self.last_name}"

    def __str__(self):
        return f"Order {self.id} - {self.first_name} {self.last_name}"

    @override
    def clean(self):
        try:
            validate_email(self.email)
        except ValidationError:
            raise ValidationError({"email": _("Invalid email address.")})

        if self.mobile_phone and self.mobile_phone == self.phone:
            raise ValidationError(
                {"mobile_phone": _("Mobile phone number cannot be the same as phone number.")}
            )

    @property
    def total_price_items(self) -> Money:
        total = self.items.annotate(
            total_price_per_item=ExpressionWrapper(
                F("price") * F("quantity"),
                output_field=MoneyField(max_digits=11, decimal_places=2),
            )
        ).aggregate(total_price=Sum("total_price_per_item"))["total_price"]

        return Money(total or 0, settings.DEFAULT_CURRENCY)

    @property
    def total_price_extra(self) -> Money:
        payment_cost = Money(0, settings.DEFAULT_CURRENCY)
        if self.pay_way and self.total_price_items.amount <= self.pay_way.free_for_order_amount.amount:
            payment_cost = self.pay_way.cost

        return self.shipping_price + payment_cost

    @property
    def full_address(self) -> str:
        return f"{self.street} {self.street_number}, {self.zipcode} {self.city}"

    def calculate_order_total_amount(self) -> Money:
        return self.total_price_items + self.total_price_extra
