from functools import cached_property
from typing import Any

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex, GinIndex
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from djmoney.money import Money
from phonenumber_field.modelfields import PhoneNumberField

from core.enum import FloorChoicesEnum, LocationChoicesEnum
from core.models import SoftDeleteModel, TimeStampMixinModel, UUIDModel
from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import OrderStatus, PaymentStatus
from order.managers.order import OrderManager


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
        _("Floor"),
        max_length=50,
        choices=FloorChoicesEnum,
        blank=True,
        default="",
    )
    location_type = models.CharField(
        _("Location Type"),
        max_length=100,
        choices=LocationChoicesEnum,
        blank=True,
        default="",
    )
    email = models.EmailField(_("Email"), max_length=255)
    first_name = models.CharField(_("First Name"), max_length=255)
    last_name = models.CharField(_("Last Name"), max_length=255)
    street = models.CharField(_("Street"), max_length=255)
    street_number = models.CharField(_("Street Number"), max_length=255)
    city = models.CharField(_("City"), max_length=255)
    zipcode = models.CharField(_("Zipcode"), max_length=255)
    place = models.CharField(_("Place"), max_length=255, blank=True, default="")
    phone = PhoneNumberField(_("Phone Number"))
    mobile_phone = PhoneNumberField(
        _("Mobile Phone Number"), null=True, blank=True, default=None
    )
    customer_notes = models.TextField(
        _("Customer Notes"), blank=True, default=""
    )
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=OrderStatus,
        default=OrderStatus.PENDING,
    )
    shipping_price = MoneyField(
        _("Shipping Price"), max_digits=11, decimal_places=2, default=0
    )
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
    status_updated_at = models.DateTimeField(
        _("Status Updated At"), auto_now=False, null=True, blank=True
    )
    payment_id = models.CharField(
        _("Payment ID"), max_length=255, blank=True, default=""
    )
    payment_status = models.CharField(
        _("Payment Status"),
        max_length=50,
        blank=True,
        choices=PaymentStatus,
        default=PaymentStatus.PENDING,
    )
    payment_method = models.CharField(
        _("Payment Method"), max_length=50, blank=True, default=""
    )
    tracking_number = models.CharField(
        _("Tracking Number"), max_length=255, blank=True, default=""
    )
    shipping_carrier = models.CharField(
        _("Shipping Carrier"), max_length=255, blank=True, default=""
    )

    objects: OrderManager = OrderManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["status"], name="order_status_ix"),
            BTreeIndex(fields=["document_type"], name="order_doc_type_ix"),
            BTreeIndex(
                fields=["status_updated_at"], name="order_status_upd_ix"
            ),
            BTreeIndex(
                fields=["payment_status"], name="order_payment_status_ix"
            ),
            BTreeIndex(fields=["user"], name="order_user_ix"),
            BTreeIndex(fields=["pay_way"], name="order_pay_way_ix"),
            BTreeIndex(fields=["country"], name="order_country_ix"),
            BTreeIndex(fields=["region"], name="order_region_ix"),
            BTreeIndex(fields=["user", "status"], name="order_user_status_ix"),
            BTreeIndex(
                fields=["status", "payment_status"],
                name="order_status_payment_ix",
            ),
            BTreeIndex(
                fields=["tracking_number"], name="order_tracking_num_ix"
            ),
            BTreeIndex(fields=["payment_id"], name="order_payment_id_ix"),
            GinIndex(
                name="order_name_search_ix",
                fields=["first_name", "last_name"],
                opclasses=["gin_trgm_ops"] * 2,
            ),
            GinIndex(
                name="order_email_search_ix",
                fields=["email"],
                opclasses=["gin_trgm_ops"],
            ),
            GinIndex(
                name="order_address_street_ix",
                fields=["street"],
                opclasses=["gin_trgm_ops"],
            ),
            GinIndex(
                name="order_address_location_ix",
                fields=["city", "place"],
                opclasses=["gin_trgm_ops"] * 2,
            ),
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._original_status = self.status

    def __str__(self) -> str:
        return f"Order {self.id} - {self.first_name} {self.last_name}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if (
            self.pk
            and hasattr(self, "_original_status")
            and self.status != self._original_status
        ):
            self.status_updated_at = timezone.now()

        if (
            not self.email
            and self.user_id is not None
            and hasattr(self, "user")
            and self.user is not None
        ):
            self.email = self.user.email

        super().save(*args, **kwargs)
        self._original_status = self.status

    def clean(self) -> None:
        errors: dict[str, list[str]] = {}

        if self.email:
            try:
                validate_email(self.email)
            except ValidationError:
                errors["email"] = [_("Enter a valid email address.")]

        if not self.zipcode:
            errors["zipcode"] = [_("Zipcode is required.")]

        required_address_fields = ["street", "street_number", "city"]
        missing_fields = [
            field
            for field in required_address_fields
            if not getattr(self, field)
        ]

        if missing_fields:
            errors["address"] = [
                _("Street, street number, and city are required.")
            ]

        if errors:
            raise ValidationError(errors)

    @property
    def total_price_items(self) -> Money:
        items_total = self.items.aggregate(
            total=Sum(F("price") * F("quantity"))
        ).get("total")

        if not items_total:
            default_currency = getattr(settings, "DEFAULT_CURRENCY", "USD")
            if self.shipping_price:
                return Money(0, self.shipping_price.currency)
            return Money(0, default_currency)

        first_item = self.items.first()
        currency = (
            first_item.price.currency
            if first_item
            else getattr(settings, "DEFAULT_CURRENCY", "USD")
        )

        return Money(amount=items_total, currency=currency)

    @property
    def total_price_extra(self) -> Money:
        return self.shipping_price

    @property
    def full_address(self) -> str:
        return f"{self.street} {self.street_number}, {self.zipcode} {self.city}"

    @property
    def customer_full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def is_paid(self) -> bool:
        return bool(
            (
                self.payment_status
                and self.payment_status == PaymentStatus.COMPLETED
            )
            or (self.paid_amount and self.paid_amount.amount > 0)
        )

    @property
    def can_be_canceled(self) -> bool:
        cancellable_statuses = [
            OrderStatus.PENDING,
            OrderStatus.PROCESSING,
        ]
        return self.status in cancellable_statuses

    @property
    def is_completed(self) -> bool:
        return self.status == OrderStatus.COMPLETED

    @property
    def is_canceled(self) -> bool:
        return self.status == OrderStatus.CANCELED

    @cached_property
    def total_price(self) -> Money:
        items_total = self.total_price_items
        extras_total = self.total_price_extra

        if items_total.currency != extras_total.currency:
            raise ValueError(
                f"Items and extras have different currencies: {items_total.currency} and {extras_total.currency}"
            )

        return Money(
            items_total.amount + extras_total.amount, items_total.currency
        )

    def calculate_order_total_amount(self) -> Money:
        return self.total_price

    def mark_as_paid(
        self,
        payment_id: str | None = None,
        payment_method: str | None = None,
    ) -> None:
        self.payment_status = PaymentStatus.COMPLETED
        if payment_id:
            self.payment_id = payment_id
        if payment_method:
            self.payment_method = payment_method

        if not self.paid_amount or self.paid_amount.amount == 0:
            self.paid_amount = self.calculate_order_total_amount()

        self.save(
            update_fields=[
                "payment_status",
                "payment_id",
                "payment_method",
                "paid_amount",
            ]
        )

    def add_tracking_info(
        self, tracking_number: str, shipping_carrier: str
    ) -> None:
        self.tracking_number = tracking_number
        self.shipping_carrier = shipping_carrier
        self.save(update_fields=["tracking_number", "shipping_carrier"])
