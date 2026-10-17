import logging
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.db.models import Count, Sum
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils import formats, timezone
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from djmoney.money import Money
from unfold.admin import TabularInline
from unfold.contrib.filters.admin import (
    AutocompleteSelectFilter,
    ChoicesDropdownFilter,
    DropdownFilter,
    MultipleChoicesDropdownFilter,
    RangeDateFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
    SliderNumericFilter,
)
from unfold.dataclasses import ActionDialog
from unfold.decorators import action, display
from unfold.enums import ActionVariant
from unfold.forms import BaseDialogForm
from unfold.widgets import (
    UnfoldAdminDecimalFieldWidget,
    UnfoldAdminTextInputWidget,
)

from admin.base import BaseModelAdmin
from admin.displays import (
    ORDER_SOURCE_TYPE_VARIANT,
    ORDER_STATUS_VARIANT,
    PAYMENT_STATUS_VARIANT,
    SHIPMENT_STATE_VARIANT,
    change_link,
    choice_label,
    format_dt,
    header_two_line,
    label_cell,
    money,
    relative_time,
)
from admin.mixins import IsSuperuserOnlyModelAdmin
from order.attribution import source_label
from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import (
    RETURN_HANDLING_STATUSES,
    OrderStatus,
)
from order.exceptions import PaymentError
from order.follow_up import (
    follow_up_days,
    follow_up_reason,
    last_carrier_event_at,
)
from order.invoicing import (
    ReceiptNotIssuable,
    generate_invoice,
    receipt_issuable,
)
from order.models.attribution import OrderAttribution
from order.models.history import OrderHistory, OrderItemHistory
from order.models.invoice import Invoice, InvoiceCounter, MyDataStatus
from order.models.item import OrderItem
from order.models.order import Order
from order.models.payment_transaction import PaymentTransaction
from order.models.retail_credit import CreditCounter, RetailCredit
from order.models.stock_log import StockLog
from order.models.viva_webhook_event import VivaWebhookEvent
from order.payment import payment_provider_label
from order.payment_ledger import (
    REPAYMENT_REFERENCE_MAX_LENGTH,
    PaymentLedger,
    is_cash_collection,
)
from order.services import OrderService
from order.taxation import breakdown_by_rate, order_vat_lines
from user.models import UserAccount

logger = logging.getLogger(__name__)

# ── Local (single-app) TextChoices variant maps ────────────────────────
# ``OrderHistoryChangeType``/``OrderItemHistoryChangeType``/
# ``MyDataStatus`` only exist in this app, so their colour maps live
# here rather than in the shared ``admin.displays`` vocabulary.

ORDER_HISTORY_CHANGE_TYPE_VARIANT: dict[str, str] = {
    OrderHistory.OrderHistoryChangeType.STATUS: "info",
    OrderHistory.OrderHistoryChangeType.PAYMENT: "success",
    OrderHistory.OrderHistoryChangeType.SHIPPING: "primary",
    OrderHistory.OrderHistoryChangeType.CUSTOMER: "warning",
    OrderHistory.OrderHistoryChangeType.ITEMS: "danger",
    OrderHistory.OrderHistoryChangeType.ADDRESS: "warning",
    OrderHistory.OrderHistoryChangeType.NOTE: "default",
    OrderHistory.OrderHistoryChangeType.REFUND: "default",
    OrderHistory.OrderHistoryChangeType.CHARGEBACK: "danger",
}

ORDER_ITEM_HISTORY_CHANGE_TYPE_VARIANT: dict[str, str] = {
    OrderItemHistory.OrderItemHistoryChangeType.QUANTITY: "info",
    OrderItemHistory.OrderItemHistoryChangeType.PRICE: "success",
    OrderItemHistory.OrderItemHistoryChangeType.REFUND: "danger",
    OrderItemHistory.OrderItemHistoryChangeType.OTHER: "default",
}

MYDATA_STATUS_VARIANT: dict[str, str] = {
    MyDataStatus.NOT_SENT: "default",
    MyDataStatus.PENDING: "info",
    MyDataStatus.SUBMITTED: "primary",
    MyDataStatus.CONFIRMED: "success",
    MyDataStatus.REJECTED: "danger",
    MyDataStatus.CANCELED: "warning",
}


class OrderSourceFilter(DropdownFilter):
    """The sources orders were actually attributed to, by name.

    Direct visits have no source (an empty value would read as "All"),
    so they are reached through the source-type filter instead.
    """

    title = _("Order source")
    parameter_name = "order_source"

    def lookups(self, request, model_admin):
        labels = {}
        for source, source_type in (
            OrderAttribution.objects.exclude(source="")
            .values_list("source", "source_type")
            .order_by("source", "source_type")
            .distinct()
        ):
            labels.setdefault(source, source_label(source, source_type))
        return sorted(labels.items(), key=lambda item: str(item[1]).lower())

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        return queryset.filter(attribution__source=self.value())


def _attribution_field(field: str, description):
    """A read-only change-form field showing one ``OrderAttribution``
    column — "-" for an order placed before attribution existed."""

    @admin.display(description=description)
    def _field(self, obj):
        attribution = getattr(obj, "attribution", None)
        if attribution is None:
            return None
        if field == "source_type":
            return attribution.get_source_type_display()
        if field == "source":
            return source_label(attribution.source, attribution.source_type)
        return getattr(attribution, field) or None

    _field.__name__ = f"attribution_{field}"
    return _field


class WholesaleOrderFilter(DropdownFilter):
    """Orders priced with a B2B customer group (the order-create paths
    stamp ``metadata["b2b_pricing"]`` whenever group pricing applied)."""

    title = _("Wholesale")
    parameter_name = "wholesale"

    def lookups(self, request, model_admin):
        return [
            ("yes", _("Wholesale-priced")),
            ("no", _("Retail-priced")),
        ]

    def queryset(self, request, queryset):
        match self.value():
            case "yes":
                return queryset.filter(metadata__has_key="b2b_pricing")
            case "no":
                return queryset.exclude(metadata__has_key="b2b_pricing")
            case _:
                return queryset


class RefundOwedFilter(DropdownFilter):
    """Cancelled orders whose payment was not (fully) returned.

    A cancel refunds a paid order through its provider, and a refund the
    provider refuses leaves the order cancelled with its payment still
    COMPLETED (``OrderService.refund_order`` records why in the order's
    history); a cancelled order refunded only in part still owes the
    rest. This is the worklist for refunding those by hand.
    """

    title = _("Refund")
    parameter_name = "refund_owed"

    def lookups(self, request, model_admin):
        return [("yes", _("Cancelled but still paid"))]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.refund_owed()
        return queryset


class FollowUpFilter(DropdownFilter):
    """Orders the shopper has waited on for the store's follow-up
    threshold (``ORDER_FOLLOW_UP_DAYS``); the rule is
    ``OrderQuerySet.needing_follow_up``, the same one behind the badge
    and the dashboard counter."""

    title = _("Needs follow-up")
    parameter_name = "follow_up"

    def lookups(self, request, model_admin):
        return [("yes", _("Yes")), ("no", _("No"))]

    def queryset(self, request, queryset):
        match self.value():
            case "yes":
                return queryset.needing_follow_up()
            case "no":
                return queryset.filter(needs_follow_up=False)
            case _:
                return queryset


class RecentOrdersFilter(DropdownFilter):
    title = _("Recent Orders")
    parameter_name = "recent_orders"

    def lookups(self, request, model_admin):
        return [
            ("today", _("Today")),
            ("week", _("This Week")),
            ("month", _("This Month")),
            ("urgent", _("Urgent (24h+ Pending)")),
        ]

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == "today":
            return queryset.filter(created_at__date=now.date())
        elif self.value() == "week":
            return queryset.filter(created_at__gte=now - timedelta(days=7))
        elif self.value() == "month":
            return queryset.filter(created_at__gte=now - timedelta(days=30))
        elif self.value() == "urgent":
            return queryset.filter(
                status=OrderStatus.PENDING,
                created_at__lt=now - timedelta(hours=24),
            )
        return queryset


class RefundOrderForm(BaseDialogForm):
    """The "Refund (amount, reason)" dialog of an order.

    The amount is checked against what the payment ledger says can still
    be refunded by ``OrderService.refund_order`` itself (under the order
    lock, so a stale dialog cannot overshoot); the dialog only offers the
    figure.
    """

    amount = forms.DecimalField(
        label=_("Amount"),
        max_digits=11,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=UnfoldAdminDecimalFieldWidget,
    )
    reason = forms.CharField(
        label=_("Reason"),
        max_length=255,
        widget=UnfoldAdminTextInputWidget,
        help_text=_("Recorded with the refund on the order's payment ledger."),
    )
    repayment_reference = forms.CharField(
        label=_("How it was paid back"),
        max_length=REPAYMENT_REFERENCE_MAX_LENGTH,
        required=False,
        widget=UnfoldAdminTextInputWidget,
        help_text=_(
            "Cash collected on delivery is paid back by you, not through a "
            "provider. Give the bank transfer id or how else it was paid; "
            "required for that part of a refund."
        ),
    )

    def __init__(self, request, object_id=None, *args, **kwargs):
        super().__init__(request, object_id, *args, **kwargs)
        self.order = Order.objects.get(pk=object_id) if object_id else None
        refundable = (
            PaymentLedger.refundable(self.order) if self.order else Decimal(0)
        )
        if refundable > 0:
            amount = self.fields["amount"]
            amount.initial = refundable
            amount.help_text = _(
                "Up to %(refundable)s can still be refunded."
            ) % {"refundable": money(refundable)}
        # Asked only where cash a courier collected can be paid back: there
        # is no provider to refund it, so staff say how they did.
        if not self._has_refundable_cash():
            del self.fields["repayment_reference"]

    def _has_refundable_cash(self) -> bool:
        return self.order is not None and any(
            is_cash_collection(balance.charge)
            for balance in PaymentLedger.balances(self.order)
        )

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get("amount")
        if (
            self.order is not None
            and amount is not None
            and "repayment_reference" in self.fields
            and not cleaned.get("repayment_reference")
            and any(
                is_cash_collection(charge)
                for charge, _share in PaymentLedger.plan_refund(
                    self.order, Money(amount, self.order.amount_due.currency)
                )
            )
        ):
            self.add_error(
                "repayment_reference",
                _(
                    "Say how the cash was paid back: this refund returns "
                    "money collected on delivery."
                ),
            )
        return cleaned


class OrderItemInline(TabularInline):
    model = OrderItem
    extra = 0
    per_page = 15
    collapsible = True
    fields = (
        "product_display",
        "quantity",
        "price_display",
        "total_display",
        "refund_status",
    )
    readonly_fields = (
        "product_display",
        "price_display",
        "total_display",
        "refund_status",
    )

    tab = True
    show_change_link = True

    @admin.display(description=_("Product"))
    def product_display(self, obj):
        if not obj.product:
            return "-"
        name = obj.product.safe_translation_getter(
            "name", any_language=True
        ) or _("Unnamed Product")
        return f"{name} (ID: {obj.product.id})"

    @admin.display(description=_("Unit Price"))
    def price_display(self, obj):
        return money(obj.price.amount)

    @admin.display(description=_("Total"))
    def total_display(self, obj):
        return money(obj.total_price.amount)

    @admin.display(description=_("Refund Status"))
    def refund_status(self, obj):
        if obj.is_refunded:
            return _("Refunded")
        if obj.refunded_quantity > 0:
            return _("Partial")
        return ""


class OrderHistoryInline(TabularInline):
    model = OrderHistory
    extra = 0
    max_num = 20
    per_page = 15
    collapsible = True
    fields = (
        "change_type_label",
        "description_display",
        "user_display",
        "created_at",
    )
    readonly_fields = fields
    ordering = ("-created_at",)

    change_type_label = choice_label(
        "change_type",
        variants=ORDER_HISTORY_CHANGE_TYPE_VARIANT,
        description=_("Change Type"),
    )

    tab = True

    def get_queryset(self, request):
        # Don't slice here — Django's inline formset later does
        # ``qs.filter(<fk>=<parent>)`` which raises
        # ``Cannot filter a query once a slice has been taken``.
        # If perf becomes a concern for orders with very long histories,
        # introduce a paginated-inline package or a SubqueryFK trick;
        # native Django inlines don't support a "show last N" pattern.
        return super().get_queryset(request).order_by("-created_at")

    @admin.display(description=_("Description"))
    def description_display(self, obj):
        description = obj.safe_translation_getter(
            "description", any_language=True
        ) or _("No description")
        return (
            description[:100] + "..." if len(description) > 100 else description
        )

    @admin.display(description=_("Changed By"))
    def user_display(self, obj):
        if obj.user:
            return obj.user.full_name or obj.user.username
        return _("System")


class PaymentTransactionInline(TabularInline):
    """The order's payment ledger, read-only.

    Every charge, cash collection and refund a provider reported, which is
    where ``Order.payment_status`` and the amount still refundable come
    from. Append-only by design, so nothing here is editable.
    """

    model = PaymentTransaction
    extra = 0
    max_num = 0
    can_delete = False
    per_page = 15
    collapsible = True
    tab = True
    fields = (
        "created_at",
        "kind",
        "amount_display",
        "provider",
        "provider_reference",
        "description",
    )
    readonly_fields = fields
    ordering = ("created_at", "id")

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Amount"))
    def amount_display(self, obj):
        return money(obj.amount.amount)


def _invoice_download_url(invoice: Invoice) -> str | None:
    """Admin-only URL to stream the stored PDF back through admin auth."""
    if not invoice.pk:
        return None
    return reverse("admin:order_invoice_download", args=[invoice.pk])


def _credit_download_url(credit: RetailCredit) -> str | None:
    """Admin-only URL to stream a credit document's PDF."""
    if not credit.pk:
        return None
    return reverse("admin:order_credit_download", args=[credit.pk])


class InvoiceInline(TabularInline):
    """Single-row inline surfacing the order's retail receipt (if any).

    Archival-only — the receipt itself is immutable once rendered
    (Greek tax law: no gaps, no edits). Admins regenerate or issue it
    via the order-level detail actions on OrderAdmin.
    """

    model = Invoice
    extra = 0
    max_num = 0
    can_delete = False
    show_change_link = True
    tab = True
    per_page = 15
    collapsible = True

    fields = (
        "invoice_number",
        "issue_date",
        "total_display",
        "currency",
        "document_status",
    )
    readonly_fields = (
        "invoice_number",
        "issue_date",
        "total_display",
        "currency",
        "document_status",
    )

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Total"))
    def total_display(self, obj):
        return money(obj.total.amount)

    @admin.display(description=_("Document"))
    def document_status(self, obj):
        if not obj or not obj.pk:
            return "—"
        if not obj.has_document():
            return _("Pending render")
        return format_html(
            '<a href="{url}" target="_blank" rel="noopener">{label}</a>',
            url=_invoice_download_url(obj) or "",
            label=_("Download PDF"),
        )


class RetailCreditInline(TabularInline):
    """The retail credit documents issued for the order's refunds."""

    model = RetailCredit
    extra = 0
    max_num = 0
    can_delete = False
    show_change_link = True
    tab = True
    per_page = 15
    collapsible = True

    fields = (
        "credit_number",
        "reason",
        "issue_date",
        "total_display",
        "mydata_status",
        "mydata_mark",
        "document_status",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Total"))
    def total_display(self, obj):
        return money(obj.total.amount)

    @admin.display(description=_("Document"))
    def document_status(self, obj):
        if not obj or not obj.pk or not obj.has_document():
            return "—"
        return format_html(
            '<a href="{url}" target="_blank" rel="noopener">{label}</a>',
            url=_credit_download_url(obj) or "",
            label=_("Download PDF"),
        )


@admin.register(Order)
class OrderAdmin(BaseModelAdmin):
    list_display = [
        "status_label",
        "follow_up_flag",
        "customer",
        "order_summary",
        "order_source",
        "payment_status_label",
        "payment_method_label",
        "shipment_state",
        "shipping_info",
        "created",
    ]
    list_filter = [
        # Multi-select: a status "group" (active, problematic, needs
        # attention) is just several statuses picked together.
        ("status", MultipleChoicesDropdownFilter),
        ("payment_status", MultipleChoicesDropdownFilter),
        "document_type",
        WholesaleOrderFilter,
        RefundOwedFilter,
        FollowUpFilter,
        RecentOrdersFilter,
        ("attribution__source_type", ChoicesDropdownFilter),
        OrderSourceFilter,
        ("created_at", RangeDateTimeFilter),
        ("status_updated_at", RangeDateTimeFilter),
        ("country", AutocompleteSelectFilter),
        ("region", AutocompleteSelectFilter),
        ("pay_way", RelatedDropdownFilter),
        "payment_method",
        # Filter Orders by carrier via the registry FK — denser than
        # a flat enum (shows the provider name) and the same lookup
        # support uses for ticket triage.
        ("shipping_provider", RelatedDropdownFilter),
        "shipping_kind",
        # Filter on the BoxNow parcel state via the OneToOne reverse
        # relation. Only fires for BoxNow pickup-point orders.
        "boxnow_shipment__parcel_state",
    ]
    search_fields = [
        "id",
        "uuid",
        "email",
        "first_name",
        "last_name",
        "phone",
        "city",
        "tracking_number",
        "payment_id",
        # BoxNow voucher / delivery-request lookup by parcel ID, so
        # support can paste a 10-digit voucher and find the order.
        "boxnow_shipment__parcel_id",
        "boxnow_shipment__delivery_request_id",
        "boxnow_shipment__locker_external_id",
    ]
    search_help_text = _(
        "Search by order ID, UUID, customer email/name/phone, city, "
        "tracking number, payment ID, or BoxNow voucher / locker ID."
    )
    autocomplete_fields = ["user", "country", "region", "pay_way"]
    readonly_fields = (
        "uuid",
        # Changed only through the actions below, never the form. A form
        # save wrote ``status`` straight to the row, skipping both the
        # transition table in ``OrderService.update_order_status`` and
        # ``OrderService.cancel_order`` — so a CANCELED picked from the
        # dropdown never restored stock (prod orders 242 and 213,
        # 2026-09-08). Every action routes through the service.
        "status",
        "follow_up_note",
        "created_at",
        "updated_at",
        "status_updated_at",
        # Stamped at checkout; the charge/redemption already happened.
        # Editing them here would desync PromotionRedemption rows, the
        # gift-card ledger, and the amount the provider captured.
        "discount_amount",
        "loyalty_discount",
        "gift_card_amount",
        "wholesale_pricing",
        "timologio_summary",
        # Shopper-supplied analytics, recorded once at checkout.
        "attribution_source_type",
        "attribution_source",
        "attribution_medium",
        "attribution_campaign",
        "attribution_referrer_host",
        "attribution_landing_path",
    )

    @admin.display(description=_("Wholesale pricing"))
    def wholesale_pricing(self, obj):
        """Which B2B group priced this order — the ONLY visibility for
        the ``metadata["b2b_pricing"]`` audit stamp."""
        info = (obj.metadata or {}).get("b2b_pricing")
        if not info:
            return _("Retail (no group pricing)")
        return _(
            # The bare % would otherwise make xgettext flag this as a
            # python-format string and msgfmt hard-fail on the Greek
            # translation (whose % is followed by a non-directive).
            # xgettext:no-python-format
            "Group “{name}” — {percent}% off net"
        ).format(
            name=info.get("group_name", "?"),
            percent=info.get("discount_percent", "?"),
        )

    fieldsets = (
        (
            _("Order Information"),
            {
                "fields": (
                    "uuid",
                    "status",
                    "follow_up_note",
                    "created_at",
                    "updated_at",
                    "status_updated_at",
                ),
                "classes": ("tab",),
            },
        ),
        (
            _("Billing / Invoice"),
            {
                "fields": (
                    "document_type",
                    "billing_company_name",
                    "billing_vat_id",
                    "billing_tax_office",
                    "billing_activity",
                    "billing_country",
                    "billing_street",
                    "billing_street_number",
                    "billing_city",
                    "billing_zipcode",
                    "wholesale_pricing",
                    "timologio_summary",
                    "timologio_invoice_number",
                    "timologio_invoice_mark",
                    "timologio_credit_number",
                    "timologio_credit_mark",
                ),
                "classes": ("tab",),
                "description": _(
                    "Buyer identity snapshotted for the invoice. Populated "
                    "only on INVOICE orders. The platform does not issue "
                    "invoices: issue it in AADE's timologio app from the "
                    "figures below, then record its number and MARK here "
                    "(and those of a credit invoice, if one follows)."
                ),
            },
        ),
        (
            _("Customer Information"),
            {
                "fields": (
                    "user",
                    "first_name",
                    "last_name",
                    "email",
                    "phone",
                ),
                "classes": ("tab",),
            },
        ),
        (
            _("Shipping Address"),
            {
                "fields": (
                    "country",
                    "region",
                    "city",
                    "street",
                    "street_number",
                    "zipcode",
                    "floor",
                    "location_type",
                    "place",
                ),
                "classes": ("tab",),
            },
        ),
        (
            _("Payment Information"),
            {
                "fields": (
                    "pay_way",
                    "payment_status",
                    "payment_method",
                    "payment_id",
                    "amount_due",
                    "discount_amount",
                    "loyalty_discount",
                    "gift_card_amount",
                ),
                "classes": ("tab",),
                "description": _(
                    "Note: Ensure all money fields use the same currency "
                    "(EUR preferred) to avoid calculation errors. "
                    "Discounts and gift-card amounts are stamped at "
                    "checkout — editing them here does NOT re-charge or "
                    "refund the customer."
                ),
            },
        ),
        (
            _("Shipping & Tracking"),
            {
                "fields": (
                    "shipping_provider",
                    "shipping_kind",
                    "shipping_price",
                    "tracking_number",
                    "shipping_carrier",
                ),
                "classes": ("tab",),
                "description": _(
                    "Shipping price currency should match item currencies "
                    "to avoid total calculation errors. Carrier-specific "
                    "voucher/tracking details are on the ACS/BoxNow "
                    "shipment tab below."
                ),
            },
        ),
        (
            _("Additional Information"),
            {
                "fields": (
                    "customer_notes",
                    "attribution_source_type",
                    "attribution_source",
                    "attribution_medium",
                    "attribution_campaign",
                    "attribution_referrer_host",
                    "attribution_landing_path",
                ),
                "classes": ("tab",),
            },
        ),
    )

    actions = [
        "mark_as_processing",
        "mark_as_shipped",
        "mark_as_delivered",
        "mark_as_completed",
        "mark_as_returned",
        "mark_as_refunded",
        "mark_as_canceled",
    ]
    # Detail header had 5 long English buttons that overflowed the
    # title bar. Group invoice + myDATA ops under one dropdown, keep
    # the shipping-voucher button top-level (it's the most-used
    # one-click op and reads cleaner with a short label).
    actions_detail = [
        "refund_payment",
        "download_shipping_voucher",
        {
            "title": _("Invoicing & myDATA"),
            "icon": "receipt_long",
            "items": [
                "issue_receipt_now",
                "regenerate_invoice",
                "send_invoice_to_mydata_now",
                "issue_credit_documents_now",
                "cancel_mydata_invoice_now",
            ],
        },
    ]
    # Per-row quick actions: avoid the detail-page round-trip for the
    # two ops the support team does most (download voucher PDF, jump
    # to the customer's other orders).
    actions_row = [
        "download_shipping_voucher",
        "view_customer_orders_row",
    ]
    inlines = [
        OrderItemInline,
        PaymentTransactionInline,
        InvoiceInline,
        RetailCreditInline,
        OrderHistoryInline,
    ]
    date_hierarchy = "created_at"
    list_select_related = [
        "user",
        "country",
        "region",
        "pay_way",
        "attribution",
    ]

    def get_inlines(self, request, obj=None):
        # Show the carrier-specific shipment inline only when the order
        # actually uses that provider — keeps the change form clean for
        # the other case (e.g. ACS orders don't get a BoxNow inline).
        # Lazy imports sidestep the circular ``order ↔ shipping_*``
        # registration cycle at app-load time.
        inlines = list(super().get_inlines(request, obj))
        if obj is None:
            return inlines

        provider_code = (
            obj.shipping_provider.code if obj.shipping_provider_id else None
        )
        # Registry-driven inline routing keyed on
        # ``obj.shipping_provider.code``. Rows with no provider set
        # (pre-registry data) get neither carrier inline; their data
        # still lives on the order itself and can be reviewed from
        # the change form fields.
        if provider_code == "boxnow":
            from shipping_boxnow.admin import (
                BoxNowShipmentOrderInline,
            )

            inlines.append(BoxNowShipmentOrderInline)
        elif provider_code == "acs":
            from shipping_acs.admin import (
                AcsShipmentOrderInline,
            )

            inlines.append(AcsShipmentOrderInline)
        return inlines

    def get_queryset(self, request):
        # ``with_total_amounts()`` annotates ``items_total`` which
        # ``Order.total_price_items`` reads from ``self.__dict__``
        # to short-circuit a per-row Sum aggregation. Without this
        # annotation, every row in ``order_summary`` fired one
        # ``SUM(price*qty)`` query + one ``price_currency`` query
        # — 106 queries for 53 orders on the changelist.
        return (
            super()
            .get_queryset(request)
            .with_total_amounts()
            .with_follow_up()
            .annotate(
                item_count=Count("items"),
                total_items_quantity=Sum("items__quantity"),
            )
            .select_related(
                "user",
                "country",
                "region",
                "pay_way",
                "attribution",
                "shipping_provider",
                "boxnow_shipment",
                "acs_shipment",
            )
        )

    status_label = choice_label(
        "status", variants=ORDER_STATUS_VARIANT, description=_("Status")
    )
    payment_status_label = choice_label(
        "payment_status",
        variants=PAYMENT_STATUS_VARIANT,
        description=_("Payment"),
    )

    @display(
        description=_("Follow-up"),
        ordering="needs_follow_up",
        label={"follow_up": "info"},
    )
    def follow_up_flag(self, obj):
        """A blue badge beside the status when the shopper has waited."""
        if not getattr(obj, "needs_follow_up", False):
            return None
        return "follow_up", _("Follow up")

    @admin.display(description=_("Follow-up"))
    def follow_up_note(self, obj):
        """Why and since when the order needs follow-up, with the
        carrier's last word on the parcel."""
        if not obj or not getattr(obj, "needs_follow_up", False):
            return "—"
        since = timezone.localdate(obj.created_at) + timedelta(
            days=follow_up_days()
        )
        last_event = last_carrier_event_at(obj)
        return format_html(
            "{} {}: {}. {}",
            _("Needs follow-up since"),
            formats.date_format(since, "SHORT_DATE_FORMAT"),
            follow_up_reason(obj),
            format_html(
                "{}: {}",
                _("Last carrier event"),
                format_dt(last_event) if last_event else _("none yet"),
            ),
        )

    # The facts an operator opens an order for, above the tabs.
    change_form_before_template = "admin/order/order_summary.html"

    def render_change_form(
        self, request, context, add=False, change=False, form_url="", obj=None
    ):
        if obj is not None:
            context["order_summary"] = self._summary(request, obj)
        return super().render_change_form(
            request, context, add, change, form_url, obj
        )

    def _summary(self, request, order) -> list[dict]:
        shipment = self.shipment_state(order)
        invoice = Invoice.objects.filter(order=order).first()
        return [
            {
                "title": _("Status"),
                "value": label_cell(
                    order.get_status_display(),
                    ORDER_STATUS_VARIANT.get(order.status, "default"),
                ),
                "footer": relative_time(order.status_updated_at),
            },
            {
                "title": _("Payment"),
                "value": label_cell(
                    order.get_payment_status_display(),
                    PAYMENT_STATUS_VARIANT.get(order.payment_status, "default"),
                ),
                "footer": self.payment_method_label(order),
            },
            {
                "title": _("Total"),
                "value": self._total(order),
                # ``get_object`` reads ``get_queryset``, which annotates it.
                "footer": ngettext(
                    "%(count)d item", "%(count)d items", order.item_count
                )
                % {"count": order.item_count},
            },
            {
                "title": _("Customer"),
                "value": change_link(
                    self.admin_site,
                    UserAccount,
                    order.user_id,
                    order.customer_full_name,
                )
                if order.user_id
                else order.customer_full_name,
                "footer": order.email,
            },
            {
                "title": _("Shipment"),
                "value": label_cell(
                    shipment[1],
                    SHIPMENT_STATE_VARIANT.get(shipment[0], "default"),
                )
                if shipment
                else "—",
                "footer": self.shipping_info(order),
            },
            self._document_card(
                order, invoice, platform_issues=self._issues_receipts(request)
            ),
        ]

    @staticmethod
    def _document_card(order, invoice, *, platform_issues: bool) -> dict:
        """The order's tax document: the receipt the platform issues, or,
        for an invoice order, the invoice staff issue in timologio. A store
        that issues its receipts outside the platform has no receipt here,
        and the card says so rather than leaving an unexplained gap."""
        if order.document_type == OrderDocumentTypeEnum.INVOICE.value:
            number = (
                order.timologio_invoice_number or order.timologio_invoice_mark
            )
            return {
                "title": _("Invoice"),
                "value": number or "—",
                "footer": _("Issued in timologio")
                if number
                else _("To be issued in timologio"),
            }
        if invoice is not None:
            return {
                "title": _("Receipt"),
                "value": invoice.invoice_number,
                "footer": label_cell(
                    invoice.get_mydata_status_display(),
                    MYDATA_STATUS_VARIANT.get(invoice.mydata_status, "default"),
                ),
            }
        return {
            "title": _("Receipt"),
            "value": "—",
            "footer": ""
            if platform_issues
            else _("Issued by the store outside the platform"),
        }

    @admin.display(description=_("To issue in timologio"))
    def timologio_summary(self, obj):
        """What staff need to issue the invoice in timologio: the buyer's
        identity and the VAT rows of the order, the figures the platform
        itself would have used (``order.taxation``)."""
        if (
            not obj
            or not obj.pk
            or obj.document_type != OrderDocumentTypeEnum.INVOICE.value
        ):
            return "—"
        address = ", ".join(
            part
            for part in (
                f"{obj.billing_street} {obj.billing_street_number}".strip(),
                f"{obj.billing_zipcode} {obj.billing_city}".strip(),
                obj.billing_country,
            )
            if part
        )
        identity = [
            (_("Company"), obj.billing_company_name),
            (_("VAT ID"), obj.billing_vat_id),
            (_("Tax office"), obj.billing_tax_office),
            (_("Business activity"), obj.billing_activity),
            (_("Address"), address),
            (_("Email"), obj.email),
        ]
        try:
            rows = breakdown_by_rate(order_vat_lines(obj))
        except ValueError:
            rows = []
        total = sum((Decimal(r["gross"]) for r in rows), Decimal(0))
        return format_html(
            "{}{}<div><strong>{}:</strong> {}</div>",
            format_html_join(
                "",
                "<div><strong>{}:</strong> {}</div>",
                ((label, value or "—") for label, value in identity),
            ),
            format_html_join(
                "",
                "<div>{}%: {} {} + {} {} = {} {}</div>",
                (
                    (
                        Decimal(r["rate"]).normalize(),
                        _("net"),
                        r["subtotal"],
                        _("VAT"),
                        r["vat"],
                        r["gross"],
                        obj.amount_due.currency.code,
                    )
                    for r in rows
                ),
            ),
            _("Total"),
            f"{total} {obj.amount_due.currency.code}",
        )

    attribution_source_type = _attribution_field(
        "source_type", _("Source type")
    )
    attribution_source = _attribution_field("source", _("Order source"))
    attribution_medium = _attribution_field("medium", _("Traffic medium"))
    attribution_campaign = _attribution_field("campaign", _("Campaign name"))
    attribution_referrer_host = _attribution_field(
        "referrer_host", _("Referrer host")
    )
    attribution_landing_path = _attribution_field(
        "landing_path", _("Landing page")
    )

    @display(description=_("Payment Method"), ordering="pay_way__provider_code")
    def payment_method_label(self, obj):
        """The PSP when the pay way charges through one, else the
        method the shopper picked (cash on delivery has no PSP).
        ``pay_way`` is select_related and both names are in memory, so
        the column costs no query."""
        provider_code = obj.pay_way.provider_code if obj.pay_way_id else ""
        return (
            payment_provider_label(provider_code)
            or obj.get_pay_way_key_display()
            or None
        )

    @display(
        description=_("Order source"),
        label=ORDER_SOURCE_TYPE_VARIANT,
        ordering="attribution__source",
    )
    def order_source(self, obj):
        attribution = getattr(obj, "attribution", None)
        if attribution is None:
            return None
        return attribution.source_type, source_label(
            attribution.source, attribution.source_type
        )

    @display(description=_("Customer"), header=True, ordering="last_name")
    def customer(self, obj):
        return header_two_line(obj.customer_full_name, obj.email)

    @staticmethod
    def _total(obj) -> str:
        try:
            # Post-discount figure — what the customer actually owes.
            return money(obj.calculate_order_total_amount().amount)
        except ValueError:
            return _(
                "items %(items)s + shipping %(shipping)s (currency mismatch)"
            ) % {
                "items": money(obj.total_price_items.amount),
                "shipping": money(obj.total_price_extra.amount),
            }

    @display(description=_("Order"), ordering="created_at")
    def order_summary(self, obj):
        item_count = getattr(obj, "item_count", 0)
        total_qty = getattr(obj, "total_items_quantity", 0) or 0
        return ngettext(
            "%(count)s item, qty %(qty)s — %(total)s",
            "%(count)s items, qty %(qty)s — %(total)s",
            item_count,
        ) % {"count": item_count, "qty": total_qty, "total": self._total(obj)}

    @display(description=_("Shipment"), label=SHIPMENT_STATE_VARIANT)
    def shipment_state(self, obj):
        """Carrier-agnostic shipment state, branching on the registry
        FK's ``code`` — mirrors ``get_inlines`` provider routing.
        """
        provider_code = (
            obj.shipping_provider.code if obj.shipping_provider_id else None
        )
        if provider_code == "acs":
            shipment = getattr(obj, "acs_shipment", None)
            if shipment is None:
                return None
            return (
                shipment.shipment_state,
                shipment.get_shipment_state_display(),
            )
        if provider_code == "boxnow":
            shipment = getattr(obj, "boxnow_shipment", None)
            if shipment is None:
                return None
            return shipment.parcel_state, shipment.get_parcel_state_display()
        return None

    @display(description=_("Shipping"), ordering="tracking_number")
    def shipping_info(self, obj):
        if obj.tracking_number:
            carrier = obj.shipping_carrier or _("Unknown carrier")
            return f"{obj.tracking_number} · {carrier} · {obj.city}"
        return f"{_('No tracking')} · {obj.city}"

    @display(description=_("Created"), ordering="created_at")
    def created(self, obj):
        return f"{format_dt(obj.created_at)} ({relative_time(obj.created_at)})"

    @action(
        description=_("Mark selected orders as processing"),
        variant=ActionVariant.PRIMARY,
        icon="play_arrow",
    )
    def mark_as_processing(self, request, queryset):
        self._bulk_update_status(
            request,
            queryset,
            OrderStatus.PROCESSING,
            _("Order %(order_id)s marked as processing"),
        )

    @action(
        description=_("Mark selected orders as shipped"),
        variant=ActionVariant.INFO,
        icon="local_shipping",
    )
    def mark_as_shipped(self, request, queryset):
        self._bulk_update_status(
            request,
            queryset,
            OrderStatus.SHIPPED,
            _("Order %(order_id)s marked as shipped"),
        )

    @action(
        description=_("Mark selected orders as delivered"),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def mark_as_delivered(self, request, queryset):
        self._bulk_update_status(
            request,
            queryset,
            OrderStatus.DELIVERED,
            _("Order %(order_id)s marked as delivered"),
        )

    @action(
        description=_("Mark selected orders as completed"),
        variant=ActionVariant.SUCCESS,
        icon="task_alt",
    )
    def mark_as_completed(self, request, queryset):
        self._bulk_update_status(
            request,
            queryset,
            OrderStatus.COMPLETED,
            _("Order %(order_id)s marked as completed"),
        )

    @action(
        description=_("Mark selected orders as returned"),
        variant=ActionVariant.WARNING,
        icon="assignment_return",
    )
    def mark_as_returned(self, request, queryset):
        # Stock is deliberately untouched: the goods come back days later
        # and may be damaged, so restocking is a staff decision on the
        # physical parcel (docs/order-system.md §5.1).
        self._bulk_update_status(
            request,
            queryset,
            OrderStatus.RETURNED,
            _("Order %(order_id)s marked as returned"),
        )

    @action(
        description=_("Mark selected orders as refunded"),
        variant=ActionVariant.WARNING,
        icon="currency_exchange",
    )
    def mark_as_refunded(self, request, queryset):
        # The order status only; the money moves through the "Refund"
        # action below (``OrderService.refund_order``) or the provider's
        # refund webhook, neither of which changes ``status``
        # (docs/order-system.md §4.2).
        self._bulk_update_status(
            request,
            queryset,
            OrderStatus.REFUNDED,
            _("Order %(order_id)s marked as refunded"),
        )

    @action(
        description=_("Refund (amount, reason)"),
        icon="currency_exchange",
        permissions=["change", "refund"],
        dialog=ActionDialog(
            title=_("Refund order"),
            description=_(
                "Pays the amount back through the payment provider, or "
                "records the cash on delivery you paid back yourself, on "
                "the order's payment ledger. A part refund can be "
                "followed by more until nothing is left. The order "
                "status and its stock are not changed."
            ),
            form_class=RefundOrderForm,
        ),
    )
    def refund_payment(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        order = Order.objects.get(pk=object_id)
        amount = Money(form.cleaned_data["amount"], order.amount_due.currency)
        reason = form.cleaned_data["reason"]
        try:
            result = OrderService.refund_order(
                order,
                amount=amount,
                reason=reason,
                repayment_reference=form.cleaned_data.get(
                    "repayment_reference", ""
                ),
            )
        except PaymentError as exc:
            # The service names the exact reason (nothing left to refund,
            # more than the remainder, not paid through a provider).
            self.message_user(request, str(exc), messages.ERROR)
        else:
            if result.paid_something:
                # Money moved, so staff are on record for it even when the
                # rest was declined.
                self.log_change(
                    request,
                    order,
                    f"Refunded {result.refunded} of {amount}: {reason}",
                )
            if result.complete and result.paid_something:
                self.message_user(
                    request,
                    _("Order %(order_id)s refunded %(amount)s.")
                    % {
                        "order_id": order.id,
                        "amount": money(result.refunded.amount),
                    },
                    messages.SUCCESS,
                )
            elif result.paid_something:
                self.message_user(
                    request,
                    _(
                        "Order %(order_id)s: %(refunded)s of %(requested)s "
                        "refunded; the rest was declined: %(reason)s"
                    )
                    % {
                        "order_id": order.id,
                        "refunded": money(result.refunded.amount),
                        "requested": money(amount.amount),
                        "reason": (result.declined or {}).get("error", ""),
                    },
                    messages.ERROR,
                )
            else:
                self.message_user(
                    request,
                    _(
                        "Order %(order_id)s was not refunded; see the order "
                        "history."
                    )
                    % {"order_id": order.id},
                    messages.ERROR,
                )
        return HttpResponse(
            headers={
                "HX-Redirect": reverse(
                    "admin:order_order_change", args=[object_id]
                ),
            }
        )

    def has_refund_permission(self, request, object_id=None):
        """Money moves from here, so only where a refund applies: an order
        staff are handling a return for (delivered, completed or
        returned). Before delivery a cancel refunds the order instead."""
        return (
            object_id is not None
            and Order.objects.filter(
                pk=object_id, status__in=RETURN_HANDLING_STATUSES
            ).exists()
        )

    def has_retail_documents_permission(self, request, object_id=None):
        """The "Invoicing & myDATA" actions exist only for a store whose
        retail receipts the platform issues. Any other store issues them
        outside the platform, so the actions are hidden and refused."""
        return self._issues_receipts(request)

    @staticmethod
    def _issues_receipts(request) -> bool:
        """Whether the platform issues this store's retail receipts
        (``MyDataConfig.is_ready``), read once per request: Unfold asks the
        permission once per action, and the order page also needs it for
        its Receipt card."""
        from order.mydata.config import load_config

        issues = getattr(request, "_platform_issues_receipts", None)
        if issues is None:
            issues = load_config().is_ready()
            request._platform_issues_receipts = issues
        return issues

    def _bulk_update_status(
        self, request, queryset, target_status, success_message
    ):
        # No outer ``transaction.atomic``: ``update_order_status`` is atomic
        # per row, so a batch-wide rollback would undo successful updates
        # just because one selected row is in a terminal/invalid state.
        # ``update_order_status`` raises ``InvalidStatusTransitionError`` (an
        # ``OrderServiceError``, NOT a ``ValueError``) for an illegal
        # transition — catching only ``ValueError`` let that bubble to a 500
        # and roll back the whole batch (G0245). Mirrors ``mark_as_canceled``.
        from order.exceptions import OrderServiceError

        for order in queryset:
            try:
                OrderService.update_order_status(order, target_status)
                self.message_user(
                    request,
                    success_message % {"order_id": order.id},
                )
            except (ValueError, OrderServiceError) as e:
                self.message_user(
                    request,
                    _("Order %(order_id)s skipped: %(reason)s")
                    % {"order_id": order.id, "reason": str(e)},
                    level="warning",
                )

    @action(
        description=_("Cancel selected orders and restore stock"),
        variant=ActionVariant.DANGER,
        icon="cancel",
    )
    def mark_as_canceled(self, request, queryset):
        # No outer ``transaction.atomic`` here: ``OrderService.cancel_order``
        # is already wrapped in its own atomic, and a batch-wide
        # rollback would undo successful cancels just because one
        # selected row is in a terminal state. Verified by prod admin
        # action on 2026-05-16 — selecting a mix of cancellable and
        # COMPLETED orders raised ``OrderCancellationError`` for the
        # COMPLETED row, bubbled up to a 500, and rolled back the
        # earlier successful cancel.
        from order.exceptions import OrderCancellationError

        for order in queryset:
            try:
                canceled, refund_info = OrderService.cancel_order(
                    order, customer_initiated=False
                )
                self.message_user(
                    request,
                    _("Order %(order_id)s marked as canceled")
                    % {"order_id": order.id},
                )
                courier_error = (
                    (canceled.metadata or {})
                    .get("cancellation", {})
                    .get("shipment_cancel", {})
                    .get("error")
                )
                if courier_error:
                    # Staff cancels go through whatever the courier says,
                    # so the parcel may still be delivered: say so here,
                    # not only in the order's metadata and history.
                    self.message_user(
                        request,
                        _(
                            "Order %(order_id)s was canceled but the courier "
                            "did not cancel its parcel (%(error)s); arrange "
                            "its return with the courier."
                        )
                        % {"order_id": order.id, "error": courier_error},
                        level="error",
                    )
                if refund_info and not refund_info["refunded"]:
                    # The order is canceled and the money is still with
                    # the shop: say so, or "marked as canceled" reads as
                    # a finished job. The cause is in the order history.
                    self.message_user(
                        request,
                        _(
                            "Order %(order_id)s was canceled but its "
                            "payment was not refunded; see the order "
                            "history."
                        )
                        % {"order_id": order.id},
                        level="error",
                    )
            except OrderCancellationError as e:
                # Order not eligible (already shipped, delivered,
                # cancelled, etc.). Skip and report — not a server
                # error.
                self.message_user(
                    request,
                    _("Order %(order_id)s skipped: %(reason)s")
                    % {"order_id": order.id, "reason": e.reason},
                    level="warning",
                )
            except Exception as e:  # pragma: no cover — defensive
                logger.exception(
                    "Bulk cancel failed for order %s",
                    order.id,
                )
                self.message_user(
                    request,
                    _(
                        "Order %(order_id)s: unexpected error "
                        "(%(error)s) — check logs"
                    )
                    % {"order_id": order.id, "error": e.__class__.__name__},
                    level="error",
                )

    # --- Invoice detail actions ---------------------------------------
    # Unfold detail-action signature is ``(self, request, object_id)`` —
    # the URL pattern is ``<path:object_id>/<url_path>/``.

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "invoice/<int:invoice_id>/download/",
                self.admin_site.admin_view(self.invoice_download_view),
                name="order_invoice_download",
            ),
            path(
                "credit/<int:credit_id>/download/",
                self.admin_site.admin_view(self.credit_download_view),
                name="order_credit_download",
            ),
        ]
        return custom + urls

    def invoice_download_view(self, request, invoice_id: int):
        """Stream the invoice PDF back through admin auth.

        Works for any storage backend (S3 / FileSystem) because we
        open the file via the storage API rather than redirecting to
        ``document_file.url``. Keeps the download gated by the admin
        login (not a one-shot signed URL).
        """
        try:
            invoice = Invoice.objects.select_related("order").get(pk=invoice_id)
        except Invoice.DoesNotExist as exc:
            raise Http404(_("Receipt not found.")) from exc
        if not invoice.has_document():
            raise Http404(_("Receipt PDF has not been generated yet."))
        return FileResponse(
            invoice.document_file.open("rb"),
            content_type="application/pdf",
            as_attachment=True,
            filename=f"{invoice.invoice_number}.pdf",
        )

    def credit_download_view(self, request, credit_id: int):
        """Stream a credit document's PDF back through admin auth."""
        try:
            credit = RetailCredit.objects.get(pk=credit_id)
        except RetailCredit.DoesNotExist as exc:
            raise Http404(_("Credit document not found.")) from exc
        if not credit.has_document():
            raise Http404(_("Credit document PDF has not been rendered yet."))
        return FileResponse(
            credit.document_file.open("rb"),
            content_type="application/pdf",
            as_attachment=True,
            filename=f"{credit.credit_number}.pdf",
        )

    def _redirect_to_order_change(self, object_id):
        return redirect(reverse("admin:order_order_change", args=[object_id]))

    @action(
        description=_("Issue receipt (myDATA)"),
        variant=ActionVariant.PRIMARY,
        icon="receipt_long",
        permissions=["change", "retail_documents"],
    )
    def issue_receipt_now(self, request, object_id):
        """Issue the order's retail receipt now (generate, register with
        AADE, email).

        The platform issues it by itself when the order ships while
        ``MYDATA_AUTO_SUBMIT`` is on; this is for a store that issues by
        hand, and for an order whose receipt never got a row (a failure
        before it was created). Exactly once per order: an order that
        already has its receipt is not issued a second one.
        """
        from order.tasks import generate_order_invoice

        try:
            order = Order.objects.get(pk=object_id)
        except Order.DoesNotExist:
            messages.error(request, _("Order not found."))
            return self._redirect_to_order_change(object_id)

        if not receipt_issuable(order):
            messages.warning(
                request,
                _(
                    "A retail receipt is issued for a receipt order once it "
                    "has shipped. Order #%(order_id)s is %(doc)s, status "
                    "%(status)s."
                )
                % {
                    "order_id": order.id,
                    "doc": order.get_document_type_display(),
                    "status": order.get_status_display(),
                },
            )
            return self._redirect_to_order_change(object_id)

        generate_order_invoice.delay(order.id)
        messages.success(
            request,
            _("Scheduled the receipt for order #%(order_id)s.")
            % {"order_id": order.id},
        )
        return self._redirect_to_order_change(object_id)

    @action(
        description=_("Issue credit documents (myDATA)"),
        variant=ActionVariant.PRIMARY,
        icon="receipt",
        permissions=["change", "retail_documents"],
    )
    def issue_credit_documents_now(self, request, object_id):
        """Issue the retail credit documents the order owes, and resubmit
        any that AADE has not registered.

        Automatic while ``MYDATA_AUTO_SUBMIT`` is on (one per refund, once
        the receipt is registered); this is for a store that issues by
        hand and for a credit that was refused or never answered.
        """
        from order.tasks import issue_pending_credits

        try:
            order = Order.objects.get(pk=object_id)
        except Order.DoesNotExist:
            messages.error(request, _("Order not found."))
            return self._redirect_to_order_change(object_id)

        issue_pending_credits.delay(order.id)
        messages.success(
            request,
            _(
                "Scheduled the credit documents for order #%(order_id)s. "
                "Nothing is issued before the receipt is registered with "
                "AADE."
            )
            % {"order_id": order.id},
        )
        return self._redirect_to_order_change(object_id)

    @action(
        description=_("Regenerate receipt (same number)"),
        variant=ActionVariant.WARNING,
        icon="refresh",
        permissions=["change", "retail_documents"],
    )
    def regenerate_invoice(self, request, object_id):
        """Re-render the receipt PDF + snapshots in place.

        Preserves the original ``invoice_number`` and ``issue_date`` so
        the sequential register stays gap-free (Greek tax law). Only
        the PDF, seller / buyer snapshots, and derived totals are
        refreshed — use this to fix a corrupted PDF or to apply an
        updated seller address/VAT ID without breaking audit traceability.
        An order with no receipt has nothing to regenerate: issuing one is
        "Issue receipt".
        """
        try:
            order = Order.objects.get(pk=object_id)
        except Order.DoesNotExist:
            messages.error(request, _("Order not found."))
            return self._redirect_to_order_change(object_id)

        try:
            invoice = generate_invoice(order, force=True)
        except ReceiptNotIssuable as refusal:
            messages.warning(request, str(refusal))
            return self._redirect_to_order_change(object_id)
        except Exception as exc:
            logger.exception(
                "Admin invoice regeneration failed for order %s", order.id
            )
            messages.error(
                request,
                _("Invoice regeneration failed: %(err)s") % {"err": str(exc)},
            )
            return self._redirect_to_order_change(object_id)

        messages.success(
            request,
            _(
                "Invoice %(num)s refreshed for order #%(order_id)s "
                "(number preserved; PDF + snapshots regenerated)."
            )
            % {"num": invoice.invoice_number, "order_id": order.id},
        )
        return self._redirect_to_order_change(object_id)

    @action(
        description=_("Send receipt to myDATA"),
        variant=ActionVariant.PRIMARY,
        icon="cloud_upload",
        permissions=["change", "retail_documents"],
    )
    def send_invoice_to_mydata_now(self, request, object_id):
        """Dispatch a manual myDATA submission for this order.

        Fire-and-forget via the Celery task so the admin response
        returns instantly. Status becomes visible on the Invoice
        admin's ``myDATA`` column within seconds.
        """
        try:
            order = Order.objects.select_related("invoice").get(pk=object_id)
        except Order.DoesNotExist:
            messages.error(request, _("Order not found."))
            return self._redirect_to_order_change(object_id)

        invoice = getattr(order, "invoice", None)
        if invoice is None or not invoice.has_document():
            messages.warning(
                request,
                _(
                    "Order #%(order_id)s has no rendered invoice yet — "
                    "generate the invoice first, then submit to myDATA."
                )
                % {"order_id": order.id},
            )
            return self._redirect_to_order_change(object_id)

        from order.tasks import send_invoice_to_mydata

        send_invoice_to_mydata.delay(order.id)
        messages.success(
            request,
            _(
                "Scheduled myDATA submission for invoice %(num)s "
                "(order #%(order_id)s). Refresh the Invoice admin to "
                "see the MARK once AADE responds."
            )
            % {"num": invoice.invoice_number, "order_id": order.id},
        )
        return self._redirect_to_order_change(object_id)

    @action(
        description=_("Cancel receipt in myDATA (issued in error)"),
        variant=ActionVariant.DANGER,
        icon="cancel",
        permissions=["change", "retail_documents"],
    )
    def cancel_mydata_invoice_now(self, request, object_id):
        """Schedule a ``CancelInvoice`` call for this order's MARK.

        Only for a receipt issued in error, and only once it has been
        registered. A sale that did not happen is undone by a credit
        document, never by cancelling the receipt.
        """
        try:
            order = Order.objects.select_related("invoice").get(pk=object_id)
        except Order.DoesNotExist:
            messages.error(request, _("Order not found."))
            return self._redirect_to_order_change(object_id)

        invoice = getattr(order, "invoice", None)
        if invoice is None or not invoice.mydata_mark:
            messages.warning(
                request,
                _(
                    "No myDATA MARK on file for order #%(order_id)s — "
                    "nothing to cancel."
                )
                % {"order_id": order.id},
            )
            return self._redirect_to_order_change(object_id)

        from order.tasks import cancel_mydata_invoice

        cancel_mydata_invoice.delay(order.id)
        messages.success(
            request,
            _(
                "Scheduled myDATA cancellation for MARK %(mark)s "
                "(order #%(order_id)s)."
            )
            % {"mark": invoice.mydata_mark, "order_id": order.id},
        )
        return self._redirect_to_order_change(object_id)

    @action(
        description=_("Download shipping voucher (PDF)"),
        variant=ActionVariant.PRIMARY,
        icon="download",
    )
    def download_shipping_voucher(self, request, object_id):
        """Stream the carrier voucher PDF for the order's shipment.

        Provider-agnostic: routes through ``ShippingService`` so it
        works for BoxNow, ACS, and any future carrier that registers
        an adapter. Mirrors the same action on each carrier-specific
        ShipmentAdmin — admins triaging orders rarely click through
        to the shipment detail page, so duplicating the button here
        saves a hop. Falls back to a flash message + redirect when
        the order has no carrier attached or the carrier hasn't yet
        minted a voucher.
        """
        from django.http import HttpResponse

        from shipping.services import ShippingService

        try:
            order = Order.objects.get(pk=object_id)
        except Order.DoesNotExist:
            messages.error(request, _("Order not found."))
            return self._redirect_to_order_change(object_id)

        adapter = ShippingService.adapter_for_order(order)
        if adapter is None:
            messages.warning(
                request,
                _(
                    "Order #%(order_id)s has no shipping carrier "
                    "attached — nothing to download."
                )
                % {"order_id": order.id},
            )
            return self._redirect_to_order_change(object_id)

        shipment = adapter.shipment_for_order(order)
        if shipment is None:
            messages.warning(
                request,
                _(
                    "No shipment row for order #%(order_id)s — the "
                    "create-shipment task has not run yet."
                )
                % {"order_id": order.id},
            )
            return self._redirect_to_order_change(object_id)

        # Carrier-agnostic "voucher minted" check. ACS exposes
        # ``voucher_no`` once ``ACS_Create_Voucher`` returns, BoxNow
        # exposes ``parcel_id`` once the delivery-request task lands.
        # When neither is set the shipment is still in a pre-creation
        # state (``pending_creation``, ``failed_creation``, etc.) and
        # hitting the carrier's label endpoint would 404 with an
        # opaque "code: ?" payload — surfacing the raw API error in
        # the admin flash bar reads as a regression. Bail early with
        # a friendly message instead.
        identifier = getattr(shipment, "voucher_no", None) or getattr(
            shipment, "parcel_id", None
        )
        if not identifier:
            state_label = getattr(
                shipment, "get_parcel_state_display", None
            ) or getattr(shipment, "get_shipment_state_display", None)
            state = state_label() if callable(state_label) else "pending"
            messages.warning(
                request,
                _(
                    "No voucher available for order #%(order_id)s — "
                    "the %(carrier)s shipment is in state "
                    "'%(state)s'. The carrier has not yet assigned a "
                    "voucher number."
                )
                % {
                    "order_id": order.id,
                    "carrier": adapter.code,
                    "state": state,
                },
            )
            return self._redirect_to_order_change(object_id)

        try:
            pdf_bytes = adapter.fetch_label_bytes(shipment)
        except Exception:
            logger.exception(
                "Admin download_shipping_voucher failed for order %s",
                order.id,
            )
            messages.error(
                request,
                _(
                    "Failed to fetch %(carrier)s voucher for order"
                    " #%(order_id)s. The carrier API responded with"
                    " an error — see backend logs for the full"
                    " traceback."
                )
                % {"carrier": adapter.code, "order_id": order.id},
            )
            return self._redirect_to_order_change(object_id)

        filename = f"{adapter.code}-voucher-{identifier}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Content-Length"] = str(len(pdf_bytes))
        return response

    @action(
        description=_("Customer's orders"),
        icon="person_search",
        variant=ActionVariant.INFO,
    )
    def view_customer_orders_row(self, request, object_id):
        """Jump from a row to the changelist filtered by the same customer.

        Saves a click for support tickets where the agent is on order N
        and needs to see the customer's other orders. We use email as
        the join key because guest orders share email but not user_id.
        """
        try:
            order = Order.objects.only("id", "user_id", "email").get(
                pk=object_id
            )
        except Order.DoesNotExist:
            messages.error(request, _("Order not found."))
            return redirect("admin:order_order_changelist")

        url = reverse("admin:order_order_changelist")
        # Prefer linking by user FK when authenticated; fall back to
        # email for guest checkouts. `q=<email>` hits search_fields.
        if order.user_id:
            return redirect(f"{url}?user__id__exact={order.user_id}")
        return redirect(f"{url}?q={order.email}")


@admin.register(OrderItem)
class OrderItemAdmin(BaseModelAdmin):
    list_display = [
        "order_link",
        "order_status",
        "product_display",
        "quantity_display",
        "pricing_info",
        "refund_status_display",
        "created_at",
    ]
    list_filter = [
        "order__status",
        "order__payment_status",
        ("product", AutocompleteSelectFilter),
        ("quantity", SliderNumericFilter),
        "is_refunded",
        ("created_at", RangeDateTimeFilter),
        "order__document_type",
    ]
    search_fields = [
        "id",
        "order__id",
        "order__email",
        "product__translations__name",
        "product__id",
        "notes",
    ]
    autocomplete_fields = ("order", "product")
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "total_price",
        # Stamped at checkout, like the order's own discounts: the tax
        # documents read it (order.discounts).
        "discount_allocation",
        "refunded_amount",
        "net_price",
    ]
    list_select_related = ["order", "order__user", "product"]

    fieldsets = (
        (
            _("Order Item Information"),
            {
                "fields": ("order", "product", "quantity", "price", "vat_rate"),
                "classes": ("wide",),
            },
        ),
        (
            _("Refund Information"),
            {
                "fields": (
                    "is_refunded",
                    "refunded_quantity",
                    "original_quantity",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Calculations"),
            {
                "fields": (
                    "total_price",
                    "discount_allocation",
                    "refunded_amount",
                    "net_price",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            _("Additional Information"),
            {
                "fields": ("notes",),
                "classes": ("collapse",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": ("id", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj is not None:
            return fieldsets
        # A new line is not asked for a rate: ``OrderItem.save`` snapshots
        # its product's current one. It cannot be shown read-only either,
        # since an unsaved instance holds a ``DatabaseDefault`` that the
        # admin has no way to format.
        return [
            (
                title,
                {
                    **options,
                    "fields": tuple(
                        name for name in options["fields"] if name != "vat_rate"
                    ),
                },
            )
            for title, options in fieldsets
        ]

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = super().get_readonly_fields(request, obj)
        # An existing line shows the rate it was sold at, which staff may
        # correct until the order's invoice is registered with AADE: from
        # then on its figures are frozen (see
        # ``invoicing._persist_invoice_row``), and an edited rate would
        # print on a regenerated PDF beside totals that no longer match.
        if (
            obj is not None
            and Invoice.objects.filter(
                order_id=obj.order_id, mydata_mark__isnull=False
            ).exists()
        ):
            return (*readonly_fields, "vat_rate")
        return readonly_fields

    def get_queryset(self, request):
        # ``product_display`` calls ``safe_translation_getter``, which
        # fires one ``ProductTranslation`` query per row without
        # prefetch.
        return (
            super()
            .get_queryset(request)
            .prefetch_related("product__translations")
        )

    @admin.display(description=_("Order"))
    def order_link(self, obj):
        return change_link(
            self.admin_site,
            Order,
            obj.order_id,
            _("Order #%(id)s") % {"id": obj.order_id},
        )

    @display(
        description=_("Order Status"),
        label=ORDER_STATUS_VARIANT,
        ordering="order__status",
    )
    def order_status(self, obj):
        return obj.order.status, obj.order.get_status_display()

    @admin.display(description=_("Product"))
    def product_display(self, obj):
        name = obj.product.safe_translation_getter(
            "name", any_language=True
        ) or _("Unnamed Product")
        return f"{name} (ID: {obj.product.id})"

    @admin.display(description=_("Quantity"))
    def quantity_display(self, obj):
        if obj.refunded_quantity > 0:
            return _("total %(qty)s, refunded %(refunded)s, net %(net)s") % {
                "qty": obj.quantity,
                "refunded": obj.refunded_quantity,
                "net": obj.net_quantity,
            }
        return f"x{obj.quantity}"

    @admin.display(description=_("Pricing"))
    def pricing_info(self, obj):
        return _("%(price)s each — total %(total)s (net %(net)s)") % {
            "price": money(obj.price.amount),
            "total": money(obj.total_price.amount),
            "net": money(obj.net_price.amount),
        }

    @admin.display(description=_("Refund Status"))
    def refund_status_display(self, obj):
        if obj.is_refunded:
            return _("Fully Refunded")
        if obj.refunded_quantity > 0:
            return _("Partial (%(refunded)s/%(qty)s)") % {
                "refunded": obj.refunded_quantity,
                "qty": obj.quantity,
            }
        return _("Active")


@admin.register(OrderHistory)
class OrderHistoryAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
    list_display = [
        "order_link",
        "change_type_label",
        "description_display",
        "user_display",
        "ip_address",
        "created_at",
    ]
    date_hierarchy = "created_at"
    list_filter = [
        "change_type",
        ("order", AutocompleteSelectFilter),
        ("user", AutocompleteSelectFilter),
        ("created_at", RangeDateTimeFilter),
    ]
    search_fields = [
        "order__id",
        "translations__description",
        "user__email",
        "user__username",
        "ip_address",
    ]
    readonly_fields = [
        "order",
        "user",
        "change_type",
        "previous_value",
        "new_value",
        "description",
        "ip_address",
        "user_agent",
        "created_at",
    ]
    list_select_related = ["order", "user"]

    change_type_label = choice_label(
        "change_type",
        variants=ORDER_HISTORY_CHANGE_TYPE_VARIANT,
        description=_("Change Type"),
    )

    @admin.display(description=_("Order"))
    def order_link(self, obj):
        return change_link(
            self.admin_site,
            Order,
            obj.order_id,
            _("Order #%(id)s") % {"id": obj.order_id},
        )

    @admin.display(description=_("Description"))
    def description_display(self, obj):
        description = obj.safe_translation_getter(
            "description", any_language=True
        ) or _("No description")
        return (
            description[:80] + "..." if len(description) > 80 else description
        )

    @admin.display(description=_("Changed By"))
    def user_display(self, obj):
        if obj.user:
            return obj.user.full_name or obj.user.username
        return _("System")


@admin.register(OrderItemHistory)
class OrderItemHistoryAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
    list_display = [
        "order_item_link",
        "change_type_label",
        "description_display",
        "user_display",
        "created_at",
    ]
    date_hierarchy = "created_at"
    list_filter = [
        "change_type",
        # AutocompleteSelectFilter — lazy XHR dropdown, no pre-fetch
        # of every OrderItem / UserAccount on changelist load.
        # ``RelatedDropdownFilter`` previously rendered
        # ``OrderItem.__str__`` per option, which reads
        # ``self.product`` + ``self.order`` per row → ×52 product +
        # ×52 order queries on every page load. Requires
        # search_fields on OrderItemAdmin + UserAdmin (both already
        # defined).
        ("order_item", AutocompleteSelectFilter),
        ("user", AutocompleteSelectFilter),
        ("created_at", RangeDateTimeFilter),
    ]
    search_fields = [
        "order_item__id",
        "order_item__order__id",
        "translations__description",
        "user__email",
    ]
    readonly_fields = [
        "order_item",
        "user",
        "change_type",
        "previous_value",
        "new_value",
        "description",
        "created_at",
    ]
    list_select_related = ["order_item", "user"]

    change_type_label = choice_label(
        "change_type",
        variants=ORDER_ITEM_HISTORY_CHANGE_TYPE_VARIANT,
        description=_("Change Type"),
    )

    def get_queryset(self, request):
        # ``description_display`` calls ``safe_translation_getter``,
        # which fires one ``OrderItemHistoryTranslation`` query per
        # row without prefetch. With 25 rows on a changelist that's
        # 25 extra queries — visible as the 115q / 191ms SQL on
        # ``/admin/order/orderitemhistory/`` in the prod sweep.
        return super().get_queryset(request).prefetch_related("translations")

    @admin.display(description=_("Order Item"))
    def order_item_link(self, obj):
        return format_html(
            "{} ({})",
            change_link(
                self.admin_site,
                OrderItem,
                obj.order_item_id,
                _("Item #%(id)s") % {"id": obj.order_item_id},
            ),
            change_link(
                self.admin_site,
                Order,
                obj.order_item.order_id,
                _("Order #%(id)s") % {"id": obj.order_item.order_id},
            ),
        )

    @admin.display(description=_("Description"))
    def description_display(self, obj):
        description = obj.safe_translation_getter(
            "description", any_language=True
        ) or _("No description")
        return (
            description[:60] + "..." if len(description) > 60 else description
        )

    @admin.display(description=_("Changed By"))
    def user_display(self, obj):
        if obj.user:
            return obj.user.full_name or obj.user.username
        return _("System")


@admin.register(StockLog)
class StockLogAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
    list_display = (
        "product",
        "operation_type",
        "stock_change",
        "order",
        "performed_by",
        "created_at",
    )
    list_filter = (
        "operation_type",
        ("created_at", RangeDateTimeFilter),
        "product__category",
    )
    search_fields = (
        "product__translations__name",
        "product__sku",
        "order__id",
        "reason",
    )
    readonly_fields = (
        "product",
        "operation_type",
        "quantity_delta",
        "stock_before",
        "stock_after",
        "order",
        "reason",
        "performed_by",
        "created_at",
    )
    date_hierarchy = "created_at"
    list_select_related = ("product", "order", "performed_by")

    @display(description=_("Stock Change"), ordering="stock_after")
    def stock_change(self, obj):
        return (
            f"{obj.stock_before} → {obj.stock_after} ({obj.quantity_delta:+d})"
        )


class HasDocumentFilter(DropdownFilter):
    title = _("Has PDF")
    parameter_name = "has_document"

    def lookups(self, request, model_admin):
        return [
            ("yes", _("Rendered")),
            ("no", _("Pending render")),
        ]

    def queryset(self, request, queryset):
        match self.value():
            case "yes":
                return queryset.exclude(document_file="").exclude(
                    document_file__isnull=True
                )
            case "no":
                return queryset.filter(document_file="") | queryset.filter(
                    document_file__isnull=True
                )
            case _:
                return queryset


@admin.register(Invoice)
class InvoiceAdmin(BaseModelAdmin):
    """Read-only archive of rendered invoices.

    Greek tax law allows no edits to an issued invoice and no gaps in
    the register, so this admin browses, searches and downloads only —
    nobody deletes here, superusers included: a deleted row is a gap.
    ``OrderAdmin``'s "Issue receipt (myDATA)" creates one; its
    "Regenerate receipt" re-renders the PDF and snapshots of an existing
    receipt in place, keeping the number and issue date
    (``order.invoicing.generate_invoice(force=True)``), and never creates
    one.
    """

    list_display = (
        "invoice_number",
        "order_link",
        "issue_date",
        "total_display",
        "currency",
        "document_badge",
        "mydata_status_label",
    )
    list_filter = (
        # ``Invoice.issue_date`` is a ``DateField`` (no time
        # component) — ``RangeDateTimeFilter`` raises TypeError on
        # DateField, hence the Date-only variant here.
        ("issue_date", RangeDateFilter),
        HasDocumentFilter,
        "mydata_status",
    )
    search_fields = (
        "invoice_number",
        "order__id",
        "order__email",
        "order__first_name",
        "order__last_name",
        "mydata_mark",
        "mydata_uid",
    )
    ordering = ("-issue_date", "-invoice_number")
    date_hierarchy = "issue_date"
    list_select_related = ("order",)

    readonly_fields = (
        "invoice_number",
        "issue_date",
        "order_link",
        "document_badge",
        "subtotal",
        "total_vat",
        "total",
        "currency",
        "vat_breakdown",
        "seller_snapshot",
        "buyer_snapshot",
        "created_at",
        "updated_at",
        # myDATA identifiers + status — populated by the submission
        # pipeline only; never edited by hand (edits would break the
        # legal paper trail).
        "mydata_status",
        "mydata_invoice_type",
        "mydata_series",
        "mydata_aa",
        "mydata_uid",
        "mydata_mark",
        "mydata_qr_url",
        "mydata_authentication_code",
        "mydata_cancellation_mark",
        "mydata_error_code",
        "mydata_error_message",
        "mydata_submitted_at",
        "mydata_confirmed_at",
    )
    fieldsets = (
        (
            _("Invoice"),
            {
                "fields": (
                    "invoice_number",
                    "issue_date",
                    "order_link",
                    "document_badge",
                    "created_at",
                    "updated_at",
                )
            },
        ),
        (
            _("Totals"),
            {
                "fields": (
                    "subtotal",
                    "total_vat",
                    "total",
                    "currency",
                    "vat_breakdown",
                )
            },
        ),
        (
            _("Snapshots"),
            {
                "fields": ("seller_snapshot", "buyer_snapshot"),
                "classes": ("collapse",),
            },
        ),
        (
            _("myDATA (AADE)"),
            {
                "fields": (
                    "mydata_status",
                    "mydata_invoice_type",
                    "mydata_series",
                    "mydata_aa",
                    "mydata_uid",
                    "mydata_mark",
                    "mydata_qr_url",
                    "mydata_authentication_code",
                    "mydata_cancellation_mark",
                    "mydata_error_code",
                    "mydata_error_message",
                    "mydata_submitted_at",
                    "mydata_confirmed_at",
                ),
                "classes": ("collapse",),
                "description": _(
                    "Populated by the automated submission pipeline "
                    "(``order.mydata``). Read-only — edit master data "
                    "(seller / buyer) and use the Order admin's 'Send to "
                    "myDATA' action to regenerate. VAT rates are fixed on "
                    "each order line at checkout and do not follow later "
                    "catalogue edits."
                ),
            },
        ),
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Order"))
    def order_link(self, obj):
        if not obj.order_id:
            return "—"
        return change_link(
            self.admin_site, Order, obj.order_id, f"#{obj.order_id}"
        )

    @admin.display(description=_("Total"))
    def total_display(self, obj):
        return money(obj.total.amount)

    @admin.display(description=_("Document"))
    def document_badge(self, obj):
        if not obj.has_document():
            return _("Pending")
        return format_html(
            '<a href="{url}" target="_blank" rel="noopener">{label}</a>',
            url=_invoice_download_url(obj) or "",
            label=_("Download"),
        )

    @display(
        description=_("myDATA"),
        label=MYDATA_STATUS_VARIANT,
        ordering="mydata_status",
    )
    def mydata_status_label(self, obj):
        label = obj.get_mydata_status_display()
        if obj.mydata_mark:
            label = f"{label} (#{obj.mydata_mark})"
        return obj.mydata_status, label


@admin.register(RetailCredit)
class RetailCreditAdmin(BaseModelAdmin):
    """Read-only register of retail credit documents (myDATA 11.4).

    Like the receipts, a credit document is a tax record: browse, search
    and download only. ``OrderAdmin``'s "Issue credit documents" resubmits
    one AADE has not registered; the "Cancel in myDATA" action here is for
    a document issued in error.
    """

    list_display = (
        "credit_number",
        "order_link",
        "reason",
        "issue_date",
        "total_display",
        "document_badge",
        "mydata_status_label",
    )
    list_filter = (
        ("issue_date", RangeDateFilter),
        "reason",
        "mydata_status",
    )
    search_fields = (
        "credit_number",
        "order__id",
        "order__email",
        "mydata_mark",
        "mydata_uid",
    )
    ordering = ("-issue_date", "-credit_number")
    date_hierarchy = "issue_date"
    list_select_related = ("order",)
    actions = ["cancel_in_mydata"]

    readonly_fields = (
        "credit_number",
        "issue_date",
        "order_link",
        "invoice",
        "reason",
        "refund",
        "document_badge",
        "subtotal",
        "total_vat",
        "total",
        "currency",
        "vat_breakdown",
        "created_at",
        "updated_at",
        "mydata_status",
        "mydata_invoice_type",
        "mydata_series",
        "mydata_aa",
        "mydata_uid",
        "mydata_mark",
        "mydata_qr_url",
        "mydata_authentication_code",
        "mydata_cancellation_mark",
        "mydata_error_code",
        "mydata_error_message",
        "mydata_submitted_at",
        "mydata_confirmed_at",
    )
    fieldsets = (
        (
            _("Credit document"),
            {
                "fields": (
                    "credit_number",
                    "issue_date",
                    "order_link",
                    "invoice",
                    "reason",
                    "refund",
                    "document_badge",
                    "created_at",
                    "updated_at",
                )
            },
        ),
        (
            _("Totals"),
            {
                "fields": (
                    "subtotal",
                    "total_vat",
                    "total",
                    "currency",
                    "vat_breakdown",
                )
            },
        ),
        (
            _("myDATA (AADE)"),
            {
                "fields": (
                    "mydata_status",
                    "mydata_invoice_type",
                    "mydata_series",
                    "mydata_aa",
                    "mydata_uid",
                    "mydata_mark",
                    "mydata_qr_url",
                    "mydata_authentication_code",
                    "mydata_cancellation_mark",
                    "mydata_error_code",
                    "mydata_error_message",
                    "mydata_submitted_at",
                    "mydata_confirmed_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Order"))
    def order_link(self, obj):
        return change_link(
            self.admin_site, Order, obj.order_id, f"#{obj.order_id}"
        )

    @admin.display(description=_("Total"))
    def total_display(self, obj):
        return money(obj.total.amount)

    @admin.display(description=_("Document"))
    def document_badge(self, obj):
        if not obj.has_document():
            return _("Pending")
        return format_html(
            '<a href="{url}" target="_blank" rel="noopener">{label}</a>',
            url=_credit_download_url(obj) or "",
            label=_("Download"),
        )

    @display(
        description=_("myDATA"),
        label=MYDATA_STATUS_VARIANT,
        ordering="mydata_status",
    )
    def mydata_status_label(self, obj):
        label = obj.get_mydata_status_display()
        if obj.mydata_mark:
            label = f"{label} (#{obj.mydata_mark})"
        return obj.mydata_status, label

    @admin.action(description=_("Cancel in myDATA (issued in error)"))
    def cancel_in_mydata(self, request, queryset):
        from order.tasks import cancel_mydata_credit

        registered = queryset.filter(mydata_mark__isnull=False)
        for credit in registered:
            cancel_mydata_credit.delay(credit.pk)
        self.message_user(
            request,
            _("Scheduled myDATA cancellation for %(count)d credit document(s).")
            % {"count": registered.count()},
        )


@admin.register(InvoiceCounter)
class InvoiceCounterAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
    """Per-year sequential invoice counter.

    Editable by superusers only — bumping ``next_number`` is an ops
    action (e.g. reserving ``INV-2026-000001..100`` for legacy imports).
    Regular staff should never touch it; a wrong value corrupts the
    sequence across all future invoices that year.
    """

    list_display = ("year", "next_number")
    ordering = ("-year",)
    readonly_fields = ()


@admin.register(CreditCounter)
class CreditCounterAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
    """Per-year sequential counter of the retail credit documents.

    Superusers only, like the receipts' counter: a wrong ``next_number``
    leaves a gap in the series AADE requires to be gapless.
    """

    list_display = ("year", "next_number")
    ordering = ("-year",)
    readonly_fields = ()


@admin.register(VivaWebhookEvent)
class VivaWebhookEventAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
    """Read-only audit trail of Viva Wallet webhook deliveries."""

    list_display = (
        "transaction_id",
        "event_type_id",
        "outcome",
        "order",
        "order_code",
        "status_id",
        "received_at",
    )
    date_hierarchy = "received_at"
    list_filter = (
        "event_type_id",
        "outcome",
        ("received_at", RangeDateTimeFilter),
    )
    search_fields = ("transaction_id", "order_code", "order__id")
    ordering = ("-received_at",)
    readonly_fields = (
        "transaction_id",
        "event_type_id",
        "order",
        "order_code",
        "status_id",
        "outcome",
        "received_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False
