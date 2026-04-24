from typing import Any, cast

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
from core.models import (
    SoftDeleteModel,
    TimeStampMixinModel,
    UUIDModel,
    MetaDataModel,
)
from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import OrderStatus, PaymentStatus
from order.managers.order import OrderManager


class Order(SoftDeleteModel, TimeStampMixinModel, UUIDModel, MetaDataModel):
    """
    Order model representing a customer purchase.

    This model tracks all order information including customer details, shipping address,
    payment information, and order status. It inherits from several mixins:
    - SoftDeleteModel: Provides soft delete functionality
    - TimeStampMixinModel: Adds created_at and updated_at timestamps
    - UUIDModel: Adds a UUID field for guest order access
    - MetaDataModel: Provides metadata and private_metadata JSON fields

    Metadata JSON Structure:
        The metadata field stores additional order information in JSON format:
        {
            "cart_snapshot": {
                "items": [...],
                "total": "...",
                "created_at": "..."
            },
            "cancellation": {
                "reason": "...",
                "cancelled_by": "...",
                "cancelled_at": "..."
            },
            "refunds": [
                {
                    "amount": "...",
                    "reason": "...",
                    "refunded_at": "...",
                    "refund_id": "..."
                }
            ],
            "stripe_checkout_session_id": "...",
            "webhook_events": [
                {
                    "event_id": "...",
                    "event_type": "...",
                    "processed_at": "..."
                }
            ]
        }

    Stock Reservation Tracking:
        The stock_reservation_ids field stores a list of StockReservation IDs that were
        converted to this order. This provides an audit trail linking temporary stock
        reservations during checkout to the final order, enabling:
        - Tracking which reservations were consumed by this order
        - Debugging stock discrepancies
        - Audit compliance for inventory management
    """

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
    payment_method_fee = MoneyField(
        _("Payment Method Fee"),
        max_digits=11,
        decimal_places=2,
        default=0,
        help_text=_(
            "Fee charged by the payment method (e.g., Cash on Delivery fee)"
        ),
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
        _("Payment ID"), max_length=255, blank=True, null=True, default=""
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
    # ── B2B billing identity (snapshotted on the order) ──────────
    # Populated only when the buyer is issuing a proper ``Τιμολόγιο
    # Πώλησης`` (``document_type=INVOICE`` + a real VAT number).
    # Kept as denormalised columns here rather than FK'd to
    # ``UserAddress`` because: (a) guests have no UserAccount, (b) the
    # invoice snapshot must survive later profile edits, (c) the tax
    # register is attached to the Order row, not the user.
    billing_vat_id = models.CharField(
        _("Billing VAT ID"),
        max_length=12,
        blank=True,
        default="",
        help_text=_(
            "Buyer's tax number (ΑΦΜ) — required when issuing an "
            "invoice (vs. a retail receipt). 9 digits for Greek ΑΦΜ, "
            "no ``EL`` / ``GR`` prefix."
        ),
    )
    billing_country = models.CharField(
        _("Billing Country"),
        max_length=2,
        blank=True,
        default="",
        help_text=_(
            "ISO 3166-1 alpha-2 country code of the buyer for tax "
            "purposes. Pairs with ``billing_vat_id``; determines "
            "which AADE invoice type (1.1 domestic, 1.2 intra-EU, "
            "1.3 third-country) applies."
        ),
    )
    tracking_number = models.CharField(
        _("Tracking Number"), max_length=255, blank=True, default=""
    )
    shipping_carrier = models.CharField(
        _("Shipping Carrier"), max_length=255, blank=True, default=""
    )
    stock_reservation_ids = models.JSONField(
        _("Stock Reservation IDs"),
        default=list,
        blank=True,
        help_text=_(
            "List of stock reservation IDs that were converted to this order. "
            "Provides audit trail linking reservations to final orders."
        ),
    )
    reminder_count = models.PositiveSmallIntegerField(
        _("Reminder Count"),
        default=0,
        help_text=_("Number of pending-order reminder emails sent."),
    )
    last_reminder_sent_at = models.DateTimeField(
        _("Last Reminder Sent At"),
        null=True,
        blank=True,
        help_text=_("Timestamp of the most recent reminder email."),
    )
    language_code = models.CharField(
        _("Language"),
        max_length=10,
        default=settings.LANGUAGE_CODE,
        help_text=_(
            "Language captured at order creation, used when rendering order emails."
        ),
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
                fields=["user", "-created_at"],
                name="order_user_created_ix",
            ),
            BTreeIndex(
                fields=["status", "payment_status"],
                name="order_status_payment_ix",
            ),
            BTreeIndex(
                fields=["payment_status", "-created_at"],
                name="order_paystatus_created_ix",
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
        # Cache tracking state so the post_save handler can detect the
        # null → set transition that fires ``order_shipment_dispatched``.
        # Using the field values (not pk) covers both fresh instances
        # and refreshed-from-DB ones without a second query.
        self._original_tracking_number = self.tracking_number
        self._original_shipping_carrier = self.shipping_carrier

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
        self._original_tracking_number = self.tracking_number
        self._original_shipping_carrier = self.shipping_carrier

    def clean(self) -> None:
        errors: dict[str, list] = {}

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
        """
        Return the sum of (price * quantity) for all order items.

        Uses the ``items_total`` annotation from ``with_total_amounts()``
        when available (set by ``for_list()`` and related querysets) to
        avoid issuing extra DB queries.  Falls back to an aggregation query
        only when the annotation is absent (e.g. ad-hoc lookups).
        """
        default_currency = getattr(settings, "DEFAULT_CURRENCY", "EUR")

        # Use pre-computed annotation when present (avoids 2 extra queries).
        annotated = self.__dict__.get("items_total")
        if annotated is not None:
            currency = (
                self.shipping_price.currency
                if self.shipping_price
                else default_currency
            )
            return Money(amount=annotated, currency=currency)

        # Fallback: aggregate from the related manager (2 queries).
        result = self.items.aggregate(total=Sum(F("price") * F("quantity")))
        items_total = result.get("total")

        if not items_total:
            if self.shipping_price:
                return Money(0, self.shipping_price.currency)
            return Money(0, default_currency)

        # Get currency from the price_currency field via a single query.
        currency_row = self.items.values_list(
            "price_currency", flat=True
        ).first()
        currency = currency_row or default_currency

        return Money(amount=items_total, currency=currency)

    @property
    def total_price_extra(self) -> Money:
        """
        Calculate total extra costs (shipping + payment method fee).

        Returns:
            Money: Sum of shipping_price and payment_method_fee

        Raises:
            ValueError: If shipping and payment fee have different currencies
        """
        extras = self.shipping_price

        if self.payment_method_fee and self.payment_method_fee.amount > 0:
            if self.shipping_price.currency != self.payment_method_fee.currency:
                raise ValueError(
                    f"Shipping and payment fee have different currencies: "
                    f"{self.shipping_price.currency} and {self.payment_method_fee.currency}"
                )
            extras = Money(
                self.shipping_price.amount + self.payment_method_fee.amount,
                self.shipping_price.currency,
            )

        return extras

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
            and (self.paid_amount and self.paid_amount.amount > 0)
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

    @property
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
                "paid_amount_currency",
            ]
        )

    def add_tracking_info(
        self, tracking_number: str, shipping_carrier: str
    ) -> None:
        self.tracking_number = tracking_number
        self.shipping_carrier = shipping_carrier
        self.save(update_fields=["tracking_number", "shipping_carrier"])

    @property
    def items_count(self) -> int:
        """
        Return the number of items in the order.

        Uses annotated value if available (from optimized queryset),
        otherwise queries the database.
        """
        if hasattr(self, "_items_count"):
            return cast(int, self._items_count) or 0
        return self.items.count()

    @property
    def total_quantity(self) -> int:
        """
        Return the total quantity of all items in the order.

        Uses annotated value if available (from optimized queryset),
        otherwise queries the database.
        """
        if hasattr(self, "_total_quantity"):
            return cast(int, self._total_quantity) or 0
        return self.items.aggregate(total=Sum("quantity"))["total"] or 0
