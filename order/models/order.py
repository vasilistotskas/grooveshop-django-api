from decimal import Decimal
from typing import Any, cast

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex, GinIndex
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from djmoney.money import Money
from phonenumber_field.modelfields import PhoneNumberField

from core.enum import FloorChoicesEnum, LocationChoicesEnum
from core.models import (
    MetaDataModel,
    SoftDeleteModel,
    TimeStampMixinModel,
    UUIDModel,
)
from core.validators import address as address_rules
from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import (
    PAYMENT_CLOSING_STATUSES,
    SETTLED_PAYMENT_STATUSES,
    OrderStatus,
    PaymentStatus,
)
from order.managers.order import OrderManager
from pay_way.enum.pay_way import PayWayEnum
from pay_way.enum.settlement import PaySettlement
from shipping.enum import ShippingKind

# Stamped on ``Order.metadata`` when a provider confirms a charge whose
# amount does not match the order total. The money HAS left the customer,
# so ``auto_cancel_stuck_pending_orders`` must leave the order for a human
# rather than closing it with ``refund_payment=False``. Lives here rather
# than beside either writer: the Viva webhook writes it and a Celery task
# reads it, and a task must not import a view.
AMOUNT_MISMATCH_FLAG = "viva_amount_mismatch"

# The partial unique index on ``Order.payment_id``. ``OrderService`` reads
# it back off a database error, to tell "this payment already has an
# order" from any other integrity failure.
PAYMENT_ID_UNIQUE_CONSTRAINT = "order_payment_id_unique"


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
        ``metadata["stock_reservation_ids"]`` lists the StockReservation IDs
        converted to this order, linking checkout-time reservations to the
        final order (audit trail, stock-discrepancy debugging).
    """

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="orders",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("User"),
    )
    pay_way = models.ForeignKey(
        "pay_way.PayWay",
        related_name="orders",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Payment Method"),
    )
    country = models.ForeignKey(
        "country.Country",
        related_name="orders",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Country"),
    )
    region = models.ForeignKey(
        "region.Region",
        related_name="orders",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Region"),
    )
    floor = models.CharField(
        _("Floor"),
        max_length=50,
        choices=FloorChoicesEnum.choices,
        blank=True,
        default="",
    )
    location_type = models.CharField(
        _("Location Type"),
        max_length=100,
        choices=LocationChoicesEnum.choices,
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
        choices=OrderStatus.choices,
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
        choices=OrderDocumentTypeEnum.choices,
        default=OrderDocumentTypeEnum.RECEIPT,
    )
    # What the customer owes for the order, fixed at creation from
    # ``calculate_order_total_amount()``: lines and extras, less every
    # deduction. It says nothing about what was collected, which is the
    # payment ledger's business (``PaymentLedger``). ``db_default`` as well
    # as ``default``: migrations run BEFORE the new image rolls, so the
    # release still serving INSERTs orders without this column.
    amount_due = MoneyField(
        _("Amount Due"),
        max_digits=11,
        decimal_places=2,
        default=0,
        db_default=Decimal(0),
        help_text=_(
            "What the customer owes for the order, fixed at creation: "
            "lines and extras, less every deduction. Not what was "
            "collected; that is on the payment ledger."
        ),
    )
    loyalty_discount = MoneyField(
        _("Loyalty Discount"),
        max_digits=11,
        decimal_places=2,
        default=0,
        help_text=_(
            "Amount deducted from the order total in exchange for loyalty "
            "points. Deducted by calculate_order_total_amount()."
        ),
    )
    discount_amount = MoneyField(
        _("Promotion Discount"),
        max_digits=11,
        decimal_places=2,
        default=0,
        help_text=_(
            "Total discount granted by promotions/coupons, snapshotted "
            "at order creation. Breakdown lives in "
            "metadata['promotions'] + PromotionRedemption rows. "
            "Deducted by calculate_order_total_amount()."
        ),
    )
    gift_card_amount = MoneyField(
        _("Gift Card Amount"),
        max_digits=11,
        decimal_places=2,
        default=0,
        help_text=_(
            "Portion of the order settled by gift-card balance. A "
            "payment, not a discount — it never reduces the taxable "
            "order value, only what the payment provider charges."
        ),
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
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
    )
    payment_hold_expires_at = models.DateTimeField(
        _("Payment Hold Expires At"),
        null=True,
        blank=True,
        help_text=_(
            "When the payment link or session last opened for this order "
            "stops being payable. The stock stays reserved until then "
            "(plus a short grace window), after which the order is "
            "canceled if still unpaid. Empty until a payment is opened."
        ),
    )
    # The gateway that took the charge; ``pay_way`` is the method the
    # shopper chose, labelled "Payment Method" like its admin section.
    payment_method = models.CharField(
        _("Payment Gateway"), max_length=50, blank=True, default=""
    )
    pay_way_key = models.CharField(
        _("Pay Way Key"),
        max_length=50,
        blank=True,
        default="",
        choices=PayWayEnum.choices,
        help_text=_(
            "Snapshot of which payment method the shopper chose, as the "
            "``PayWayEnum`` key. Distinct from ``payment_method``, which "
            "records the gateway that processed the charge (``stripe``, "
            "``viva_wallet``, ``acs_cod``) and is written later by the "
            "payment-confirmation handlers — two different questions. "
            "Snapshotted because ``pay_way`` is ``SET_NULL``: deleting "
            "the row would otherwise erase what the customer picked, "
            "and renaming its key would rewrite history. Also what the "
            "storefront renders, since the API cannot localise (see "
            "``PayWay.display_name``)."
        ),
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
    # Company requisites for the invoice document (PDF + the tenant's
    # ERP handoff). Deliberately NOT in the myDATA counterpart block —
    # AADE forbids <name>/<address> for domestic counterparts (error
    # 220); the registry resolves them from the ΑΦΜ.
    billing_company_name = models.CharField(
        _("Billing Company Name"), max_length=255, blank=True, default=""
    )
    billing_tax_office = models.CharField(
        _("Billing Tax Office (ΔΟΥ)"), max_length=100, blank=True, default=""
    )
    billing_activity = models.CharField(
        _("Billing Business Activity"),
        max_length=255,
        blank=True,
        default="",
    )
    # The company's registered address (έδρα) — the delivery address is
    # NOT the invoice address for most businesses. Copied from the
    # shipping fields at validation time when the buyer says they match.
    billing_street = models.CharField(
        _("Billing Street"), max_length=255, blank=True, default=""
    )
    billing_street_number = models.CharField(
        _("Billing Street Number"), max_length=50, blank=True, default=""
    )
    billing_city = models.CharField(
        _("Billing City"), max_length=100, blank=True, default=""
    )
    billing_zipcode = models.CharField(
        _("Billing Zipcode"), max_length=20, blank=True, default=""
    )
    # ── The invoice staff issue by hand in AADE's timologio app ──
    # The platform does not issue invoices (AADE decision Α.1128/2025
    # art. 1(2)); for a ``document_type=INVOICE`` order staff issue it in
    # timologio from the billing fields above and record here what AADE
    # registered, so the order shows the document that exists for it.
    timologio_invoice_mark = models.BigIntegerField(
        _("Invoice MARK (timologio)"),
        null=True,
        blank=True,
        help_text=_("MARK of the invoice issued in the timologio app."),
    )
    timologio_invoice_number = models.CharField(
        _("Invoice number (timologio)"),
        max_length=64,
        blank=True,
        default="",
        db_default="",
        help_text=_("Series and number of the invoice issued in timologio."),
    )
    timologio_credit_mark = models.BigIntegerField(
        _("Credit invoice MARK (timologio)"),
        null=True,
        blank=True,
        help_text=_(
            "MARK of a credit invoice issued in timologio for this order."
        ),
    )
    timologio_credit_number = models.CharField(
        _("Credit invoice number (timologio)"),
        max_length=64,
        blank=True,
        default="",
        db_default="",
        help_text=_("Series and number of that credit invoice."),
    )
    tracking_number = models.CharField(
        _("Tracking Number"), max_length=255, blank=True, default=""
    )
    shipping_carrier = models.CharField(
        _("Shipping Carrier"), max_length=255, blank=True, default=""
    )
    shipping_provider = models.ForeignKey(
        "shipping.ShippingProvider",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name=_("Shipping Provider"),
        help_text=_(
            "Carrier handling this order. Null for legacy rows where "
            "fulfilment is handled outside any registered provider."
        ),
    )
    shipping_kind = models.CharField(
        _("Shipping Kind"),
        max_length=32,
        choices=ShippingKind.choices,
        default=ShippingKind.HOME_DELIVERY,
        db_index=True,
        help_text=_("Generic fulfilment kind, independent of provider."),
    )
    estimated_delivery = models.DateField(
        _("Estimated Delivery"),
        null=True,
        blank=True,
        help_text=_(
            "Latest expected delivery date, fixed when the order was "
            "placed from the chosen shipping rate, the dispatch cutoff "
            "and business days. Null when the rate advertises no "
            "estimate. A promise made at checkout: later rate edits do "
            "not move it."
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

    @property
    def is_first_order(self) -> bool:
        """Whether this is the customer's first order with the store.

        Feeds Google Ads' ``new_customer`` conversion parameter, which
        Google asks to be "calculated dynamically" — a hardcoded value
        skews new-customer acquisition reporting for every campaign.

        Identity is the account when there is one, else the email
        (case-insensitive), so a guest who later registers is still one
        customer. Canceled orders do not count: a checkout that never
        completed is not a prior purchase. Queries the table once; the
        only reader is the order detail on the success page.
        """
        if self.user_id is not None:
            prior = Order.objects.filter(user_id=self.user_id)
        else:
            prior = Order.objects.filter(
                user__isnull=True, email__iexact=self.email
            )
        return not (
            prior.exclude(pk=self.pk)
            .exclude(status=OrderStatus.CANCELED)
            .filter(created_at__lt=self.created_at)
            .exists()
        )

    class Meta(TypedModelMeta):
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")
        ordering = ["-created_at"]
        indexes = [
            # Declaring ``indexes`` REPLACES the abstract parents' list
            # rather than extending it, so an index a mixin declares
            # only reaches the tables that splat it back in.
            *TimeStampMixinModel.Meta.indexes,
            # MetaDataModel's ``metadata`` half, named to match what it
            # would have produced. It was missing entirely, which left
            # the Viva webhook's ``metadata__contains`` lookup — run on
            # every delivery, by a caller that retries hourly — as a
            # sequential scan of the whole orders table.
            #
            # Its ``private_metadata`` half is deliberately NOT taken:
            # no query in this codebase reads that column on an order
            # (``MetaDataFilterMixin``, which exposes the only lookup,
            # is not in OrderFilter's bases), and a GIN index still
            # costs a pending-list write on every order INSERT and
            # UPDATE. See tests/unit/core/test_abstract_model_indexes.py.
            GinIndex(fields=["metadata"], name="order_meta_ix"),
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
                fields=["status", "-created_at"],
                name="order_status_created_ix",
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
        constraints = [
            # One order per provider payment. Every webhook resolves its
            # order with ``.filter(payment_id=...).first()``, so a second
            # order on the same intent would never be paid, would hold
            # its stock until the stuck-order sweep, and would email the
            # shopper. "No payment id yet" is spelled both "" and NULL
            # and must stay repeatable, hence the condition.
            models.UniqueConstraint(
                fields=["payment_id"],
                condition=(
                    models.Q(payment_id__isnull=False)
                    & ~models.Q(payment_id="")
                ),
                name=PAYMENT_ID_UNIQUE_CONSTRAINT,
            ),
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._original_status = self.status
        # Payment status is snapshotted too so the post_save handler can
        # detect payment transitions (they can occur without a status
        # change, e.g. a refund) for the agent-gateway event push.
        self._original_payment_status = self.payment_status
        # Cache tracking state so the post_save handler can detect the
        # null → set transition that fires ``order_shipment_dispatched``.
        # Using the field values (not pk) covers both fresh instances
        # and refreshed-from-DB ones without a second query.
        self._original_tracking_number = self.tracking_number
        self._original_shipping_carrier = self.shipping_carrier
        # Lets ``save()`` tell "the pay way was just chosen/changed"
        # from "it is unchanged", so the ``pay_way_key`` snapshot is
        # taken once and never silently rewritten afterwards.
        self._original_pay_way_id = self.pay_way_id

    def __str__(self) -> str:
        return gettext("Order %(id)s - %(first_name)s %(last_name)s") % {
            "id": self.id,
            "first_name": self.first_name,
            "last_name": self.last_name,
        }

    def save(self, *args: Any, **kwargs: Any) -> None:
        status_changed = bool(
            self.pk
            and hasattr(self, "_original_status")
            and self.status != self._original_status
        )
        if status_changed:
            self.status_updated_at = timezone.now()
        # An order that leaves unpaid owes nothing any more, so its
        # financial state is final too. Decided HERE, in the one save
        # every cancel path shares (the service, the admin form, a
        # script), rather than in the cancel cascade: a second save
        # from inside ``post_save`` runs while ``_original_status``
        # still holds the old status and re-fires the whole status
        # transition — emails included. 78 canceled orders on tenant
        # #1 read "Pending" forever before this (2026-09-18).
        #
        # RETURNED closes the same way. A parcel the customer refused
        # or never collected comes back with the cash still uncollected,
        # so the order owes nothing either; its only other exit,
        # REFUNDED, is for money that actually moved. 34 returned COD
        # orders on tenant #1 read "Pending" forever before this
        # (2026-09-20).
        payment_settled = (
            status_changed
            and self.status in PAYMENT_CLOSING_STATUSES
            and self.settle_unpaid_payment()
        )

        if (
            not self.email
            and self.user_id is not None
            and hasattr(self, "user")
            and self.user is not None
        ):
            self.email = self.user.email

        pay_way_key_changed = self._snapshot_pay_way_key()

        # Django writes an ``auto_now`` column only when it appears in
        # ``update_fields``, so every partial save left ``updated_at``
        # holding the creation timestamp. Production order 264 ran
        # PENDING -> PROCESSING -> SHIPPED across two days and still
        # reported ``updated_at`` equal to its ``created_at``.
        #
        # That is not cosmetic. ``updated_at`` is a documented API filter
        # (order/filters.py exposes gte/lte/date), so "orders changed
        # since X" silently omitted every status change. Worse,
        # ``auto_cancel_stuck_pending_orders`` selects the FAILED-payment
        # bucket with ``updated_at__lt=now - GRACE``, intending a retry
        # window measured from the failure; measured from creation
        # instead, any payment failing more than GRACE after checkout was
        # cancellable on the very next run, with no grace at all.
        #
        # Fixed here rather than at ~35 call sites so it cannot be missed
        # again — order/stock.py and the shipping services already pass
        # "updated_at" by hand, which is the convention Order's own saves
        # simply never followed. ``status_updated_at`` gets the same
        # treatment: this method ASSIGNS it above, and a caller passing
        # update_fields=["status"] would otherwise drop that write while
        # ``_original_status`` below still consumed the transition, and
        # ``pay_way_key`` the same again.
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            fields = set(update_fields)
            fields.add("updated_at")
            if status_changed:
                fields.add("status_updated_at")
            if payment_settled:
                fields.add("payment_status")
            if pay_way_key_changed:
                fields.add("pay_way_key")
            kwargs["update_fields"] = fields

        super().save(*args, **kwargs)
        self._original_status = self.status
        self._original_payment_status = self.payment_status
        self._original_tracking_number = self.tracking_number
        self._original_shipping_carrier = self.shipping_carrier
        self._original_pay_way_id = self.pay_way_id

    def settle_unpaid_payment(self) -> bool:
        """Settle the financial state of an order that closes unpaid.

        PENDING / PROCESSING / FAILED become CANCELED. A settled state
        (COMPLETED, REFUNDED, PARTIALLY_REFUNDED, CANCELED) is never
        touched — a paid order moves to REFUNDED through the refund,
        and the webhook guards rely on settled states staying put.
        In-memory only; ``save()`` calls it on the CANCELED and RETURNED
        transitions (``PAYMENT_CLOSING_STATUSES``). Returns True when it
        changed something.
        """
        if self.payment_status in SETTLED_PAYMENT_STATUSES:
            return False
        self.payment_status = PaymentStatus.CANCELED
        return True

    def _snapshot_pay_way_key(self) -> bool:
        """Freeze which payment method the shopper chose.

        Done here, not at the order-creation call sites, because there
        are several of them (cart checkout, the agent/UCP surface,
        reorder) and a new one must not be able to forget. Same reason
        ``updated_at`` is handled in this method rather than at ~35
        call sites.

        Written when the snapshot is empty or the pay way genuinely
        changed — an operator re-pointing an order's ``pay_way`` in the
        admin means the label should follow. Never rewritten otherwise,
        so a later rename of the PayWay row (``pay_way`` migration 0022
        renamed one) cannot retroactively change what a historical
        order says the customer picked.

        Returns True when the value changed, so ``save()`` can add the
        column to a partial ``update_fields`` — without that, every
        partial save would silently drop the write.
        """
        if self.pay_way_id is None:
            return False

        pay_way_changed = self.pay_way_id != getattr(
            self, "_original_pay_way_id", None
        )
        if self.pay_way_key and not pay_way_changed:
            return False

        # ``self.pay_way`` is already loaded on every read path
        # (``for_list``/``for_detail`` select_related it) and costs one
        # query on a write path that only set ``pay_way_id``.
        pay_way = self.pay_way
        key = pay_way.key
        if key == self.pay_way_key:
            return False

        self.pay_way_key = key
        return True

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

        if not errors:
            # Postcode format + street/number mix-ups, only for an
            # address that is being entered or changed.
            errors.update(address_rules.model_address_errors(self))

        errors.update(self._payment_consistency_errors())

        if errors:
            raise ValidationError(errors)

    def _payment_consistency_errors(self) -> dict[str, list]:
        """A COMPLETED payment with nothing due must be fully covered.

        ``amount_due`` is legitimately 0.00 on a COMPLETED order only
        when deductions — promotion, loyalty redemption, gift card —
        cover the whole total (see ``is_paid``); ``calculate_order_total_
        amount`` is exactly that remainder. Anything else is a payment
        booked on an order whose amount due was never set: the admin
        form, which edits both fields, is the path this guards (the demo
        fixtures that produced eight such rows no longer go through
        ``save`` at all).

        Not a ``CheckConstraint``: the total is a sum over the order's
        lines, which a row constraint cannot see. An unsaved order has
        no lines to total, so it is not judged.
        """
        if self.payment_status != PaymentStatus.COMPLETED or self.pk is None:
            return {}
        if self.amount_due.amount > 0:
            return {}
        if self.calculate_order_total_amount().amount == 0:
            return {}
        return {
            "amount_due": [
                _(
                    "A completed payment needs an amount due, unless "
                    "discounts, loyalty points or gift cards cover the "
                    "whole order total."
                )
            ]
        }

    @property
    def total_price_items(self) -> Money:
        """
        Return the sum of (price * quantity) for all order items.

        Uses the ``items_total`` / ``items_currencies`` annotations from
        ``with_total_amounts()`` when available (set by ``for_list()`` and
        related querysets) to avoid extra DB queries. Falls back to an
        aggregation query only when they are absent (e.g. ad-hoc lookups).
        Either way the total carries the items' own currency, and lines in
        different currencies raise ``ValueError``.

        The annotation is a ``SUM`` over the items, so an order with none
        carries it as ``NULL``: presence of the key, not its value, says
        the queryset annotated it. Testing the value sent every item-less
        row of a list back to the fallback — one aggregate per row.
        """
        default_currency = getattr(settings, "DEFAULT_CURRENCY", "EUR")

        if "items_total" in self.__dict__:
            items_total = self.__dict__["items_total"]
            currencies = set(self.__dict__.get("items_currencies") or ())
        else:
            items_total = self.items.aggregate(
                total=Sum(F("price") * F("quantity"))
            )["total"]
            currencies = (
                set(
                    self.items.order_by()
                    .values_list("price_currency", flat=True)
                    .distinct()
                )
                if items_total
                else set()
            )

        if not items_total:
            if self.shipping_price:
                return Money(0, self.shipping_price.currency)
            return Money(0, default_currency)

        # ``SUM`` ignores ``price_currency``, so a line in another
        # currency would be added in silently. Refuse it, as
        # ``total_price`` refuses items and extras that disagree.
        if len(currencies) > 1:
            raise ValueError(
                "Order items have different currencies: "
                + ", ".join(sorted(currencies))
            )
        currency = next(iter(currencies), None) or default_currency

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
        """True when the order owes nothing.

        ``payment_status`` is the settlement authority — only
        ``mark_as_paid`` and the provider webhooks set it COMPLETED —
        and the whole test: a settled order can legitimately owe zero
        when deductions (gift card, 100% promotion, full loyalty
        redemption) cover the whole total. ``OrderService`` settles
        those without ever involving a provider, and ``amount_due`` is
        exactly 0.00 there, so a test on the amount would report the
        order unpaid forever, ``awaits_online_payment`` would stay True,
        and the checkout endpoints would open a zero-amount provider
        session for it.

        A COMPLETED order can only carry a zero ``amount_due`` in that
        case: ``mark_as_paid`` backfills the field from the order total
        whenever it is zero, so any order that owed money ends up
        positive.
        """
        return self.payment_status == PaymentStatus.COMPLETED

    @property
    def remaining_amount(self) -> Money:
        """What the customer still has to pay: ``amount_due`` until the
        payment is settled, nothing after.

        Read from ``payment_status``, never from the payment ledger: the
        ledger holds no row for an order settled without a provider (a
        payment staff recorded by hand, cash collected under a
        ``payment_method`` the ledger backfill does not key on, the demo
        seed), and such an order would read as unpaid.

        A settled status owes nothing, whichever it is
        (``SETTLED_PAYMENT_STATUSES``). A refund does not create an amount
        owed: PARTIALLY_REFUNDED and REFUNDED stay at zero. CANCELED owes
        nothing either: ``Order.save`` settles an order that closes unpaid
        to CANCELED (``PAYMENT_CLOSING_STATUSES``), because no payment
        will ever be collected for it.
        """
        if self.payment_status in SETTLED_PAYMENT_STATUSES:
            return Money(0, self.amount_due.currency)
        return self.amount_due

    @property
    def awaits_online_payment(self) -> bool:
        """True when the shopper still owes money through a hosted flow.

        Hosted providers (Viva, Stripe) mint the Order FIRST and only
        then redirect the shopper off-site to pay, so between those two
        moments the order exists but nothing has been charged. The cart
        must survive that window: clearing it at creation meant a
        shopper who abandoned the hosted page — or simply pressed Back —
        came back to an empty basket and a stranded PENDING order.
        Production data for the five months to 2026-08-08: 35 Viva
        orders ended CANCELED with payment still PENDING against 27
        COMPLETED, so the abandoned path was the common one.

        Cash-on-delivery is never "awaiting" — nothing is owed online,
        so those carts still clear the moment the order is placed.
        """
        pay_way = self.pay_way
        if (
            pay_way is None
            or PaySettlement(pay_way.settlement) != PaySettlement.ONLINE
        ):
            return False
        return not self.is_paid

    @property
    def can_be_canceled(self) -> bool:
        cancellable_statuses = [
            OrderStatus.PENDING,
            OrderStatus.PROCESSING,
        ]
        return self.status in cancellable_statuses

    @property
    def can_be_canceled_by_customer(self) -> bool:
        """Whether the customer's cancel button should be on offer.

        A customer cancel asks the courier to take the parcel back
        before the order changes (``OrderService.cancel_order``), so on
        top of the status rule the offer ends once local data shows the
        courier would refuse: an ACS voucher already on an issued pickup
        list, a BoxNow parcel past ``NEW``. Read from local rows only —
        never a carrier call. Staff and system cancels are not bound by
        the courier, so they stay on :attr:`can_be_canceled`.
        """
        from shipping.services import ShippingService

        return self.can_be_canceled and ShippingService.can_cancel_shipment(
            self
        )

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
        """What the customer actually owes: line items + extras, minus
        any loyalty redemption.

        This is the ONE amount the shopper is charged, the order-create
        verification compares against, and the payment webhooks check.
        The discount used to live only in ``amount_due`` while every
        charge site read the undiscounted ``total_price``: a shopper who
        spent 500 points for EUR 5 off a EUR 50 cart saw EUR 45 in the
        sidebar, had the points burnt, and was charged EUR 50 by both
        Stripe and Viva — and the webhook amount guard, reading the same
        undiscounted figure, endorsed the overcharge instead of catching
        it. Cash on delivery disagreed and collected the discounted
        figure, which is what made it a bug rather than a pricing
        policy.

        Kept separate from ``total_price`` so the discounts stay
        visible lines rather than being folded into the subtotal.
        """
        total = self.total_price
        deductions = sum(
            (
                deduction.amount
                for deduction in (
                    self.discount_amount,
                    self.loyalty_discount,
                    self.gift_card_amount,
                )
                if deduction and deduction.amount > 0
            ),
            Decimal(0),
        )
        if deductions <= 0:
            return total
        return Money(max(0, total.amount - deductions), total.currency)

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

        if not self.amount_due:
            self.amount_due = self.calculate_order_total_amount()

        self.save(
            update_fields=[
                "payment_status",
                "payment_id",
                "payment_method",
                "amount_due",
                "amount_due_currency",
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
