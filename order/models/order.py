from django.conf import settings
from django.db import models
from django.db.models import ExpressionWrapper
from django.db.models import F
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from djmoney.money import Money
from phonenumber_field.modelfields import PhoneNumberField

from core.models import TimeStampMixinModel
from core.models import UUIDModel
from order.enum.document_type_enum import OrderDocumentTypeEnum
from order.enum.status_enum import OrderStatusEnum
from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum


class Order(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="user_order",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    pay_way = models.ForeignKey(
        "pay_way.PayWay",
        related_name="order_pay_way",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    country = models.ForeignKey(
        "country.Country",
        related_name="order_country",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    region = models.ForeignKey(
        "region.Region",
        related_name="order_region",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    floor = models.CharField(
        max_length=50,
        choices=FloorChoicesEnum.choices,
        null=True,
        blank=True,
        default=None,
    )
    location_type = models.CharField(
        max_length=100,
        choices=LocationChoicesEnum.choices,
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
    phone = PhoneNumberField(_("Phone Number"))
    mobile_phone = PhoneNumberField(
        _("Mobile Phone Number"), null=True, blank=True, default=None
    )
    customer_notes = models.TextField(_("Customer Notes"), null=True, blank=True)
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=OrderStatusEnum.choices,
        default=OrderStatusEnum.PENDING,
    )
    shipping_price = MoneyField(
        _("Shipping Price"), max_digits=19, decimal_places=4, default=0
    )
    document_type = models.CharField(
        _("Document Type"),
        max_length=100,
        choices=OrderDocumentTypeEnum.choices,
        default=OrderDocumentTypeEnum.RECEIPT,
    )
    paid_amount = MoneyField(
        _("Paid Amount"),
        max_digits=19,
        decimal_places=4,
        null=True,
        default=0,
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")
        ordering = ["-created_at"]

    def __str__(self):
        return self.first_name

    @property
    def total_price_items(self) -> Money:
        if not hasattr(self, "order_item_order") or not self.order_item_order:
            return Money(0, settings.DEFAULT_CURRENCY)

        total_items_price = self.order_item_order.aggregate(
            total_price=ExpressionWrapper(
                Sum(F("price") * F("quantity")),
                output_field=MoneyField(max_digits=19, decimal_places=4),
            )
        )["total_price"]

        if not total_items_price:
            return Money(0, settings.DEFAULT_CURRENCY)

        return Money(total_items_price, settings.DEFAULT_CURRENCY)

    @property
    def total_price_extra(self) -> Money:
        pay_way = self.pay_way

        if not pay_way:
            return self.shipping_price

        if self.total_price_items.amount > pay_way.free_for_order_amount.amount:
            payment_cost = Money(0, settings.DEFAULT_CURRENCY)
        else:
            payment_cost = pay_way.cost

        return self.shipping_price + payment_cost

    @property
    def full_address(self) -> str:
        return f"{self.street} {self.street_number}, {self.zipcode} {self.city}"

    def calculate_order_total_amount(self) -> Money:
        return self.total_price_items + self.total_price_extra
