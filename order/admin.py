import logging
from datetime import timedelta

from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Count, Sum
from django.http import FileResponse, Http404
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from unfold.admin import TabularInline

from admin.base import BaseModelAdmin
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateFilter,
    RangeDateTimeFilter,
    RangeNumericListFilter,
    RelatedDropdownFilter,
    SliderNumericFilter,
)
from unfold.decorators import action
from unfold.enums import ActionVariant

from admin.mixins import IsSuperuserOnlyModelAdmin
from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import OrderStatus, PaymentStatus
from order.invoicing import generate_invoice
from order.models.history import OrderHistory, OrderItemHistory
from order.models.invoice import Invoice, InvoiceCounter
from order.models.item import OrderItem
from order.models.order import Order
from order.models.stock_log import StockLog
from order.models.viva_webhook_event import VivaWebhookEvent
from order.services import OrderService

logger = logging.getLogger(__name__)


class OrderStatusGroupFilter(DropdownFilter):
    title = _("Status Group")
    parameter_name = "status_group"

    def lookups(self, request, model_admin):
        return [
            ("active", _("Active Orders (Pending/Processing)")),
            ("fulfillment", _("In Fulfillment (Shipped/Delivered)")),
            ("completed", _("Completed Orders")),
            ("problematic", _("Problematic (Canceled/Returned/Refunded)")),
        ]

    def queryset(self, request, queryset):
        filter_value = self.value()

        match filter_value:
            case "active":
                filter_kwargs = {
                    "status__in": [OrderStatus.PENDING, OrderStatus.PROCESSING]
                }
            case "fulfillment":
                filter_kwargs = {
                    "status__in": [OrderStatus.SHIPPED, OrderStatus.DELIVERED]
                }
            case "completed":
                filter_kwargs = {"status": OrderStatus.COMPLETED}
            case "problematic":
                filter_kwargs = {
                    "status__in": [
                        OrderStatus.CANCELED,
                        OrderStatus.RETURNED,
                        OrderStatus.REFUNDED,
                    ]
                }
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


class PaymentStatusFilter(DropdownFilter):
    title = _("Payment Status")
    parameter_name = "payment_status_filter"

    def lookups(self, request, model_admin):
        return [
            ("completed", _("Completed")),
            ("pending", _("Pending")),
            ("processing", _("Processing")),
            ("failed", _("Failed")),
            ("refunded", _("Refunded")),
            ("partially_refunded", _("Partially Refunded")),
            ("canceled", _("Canceled")),
            ("needs_attention", _("Needs Attention (Failed/Pending)")),
        ]

    def queryset(self, request, queryset):
        filter_value = self.value()

        match filter_value:
            case "completed":
                filter_kwargs = {"payment_status": PaymentStatus.COMPLETED}
            case "pending":
                filter_kwargs = {"payment_status": PaymentStatus.PENDING}
            case "processing":
                filter_kwargs = {"payment_status": PaymentStatus.PROCESSING}
            case "failed":
                filter_kwargs = {"payment_status": PaymentStatus.FAILED}
            case "refunded":
                filter_kwargs = {"payment_status": PaymentStatus.REFUNDED}
            case "partially_refunded":
                filter_kwargs = {
                    "payment_status": PaymentStatus.PARTIALLY_REFUNDED
                }
            case "canceled":
                filter_kwargs = {"payment_status": PaymentStatus.CANCELED}
            case "needs_attention":
                filter_kwargs = {
                    "payment_status__in": [
                        PaymentStatus.FAILED,
                        PaymentStatus.PENDING,
                    ]
                }
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


class DocumentTypeFilter(DropdownFilter):
    title = _("Document Type")
    parameter_name = "document_type_filter"

    def lookups(self, request, model_admin):
        return [
            ("receipt", _("Receipt")),
            ("invoice", _("Invoice")),
            ("proforma", _("Proforma Invoice")),
            ("shipping_label", _("Shipping Label")),
            ("return_label", _("Return Label")),
            ("credit_note", _("Credit Note")),
        ]

    def queryset(self, request, queryset):
        filter_value = self.value()

        match filter_value:
            case "receipt":
                filter_kwargs = {"document_type": OrderDocumentTypeEnum.RECEIPT}
            case "invoice":
                filter_kwargs = {"document_type": OrderDocumentTypeEnum.INVOICE}
            case "proforma":
                filter_kwargs = {
                    "document_type": OrderDocumentTypeEnum.PROFORMA
                }
            case "shipping_label":
                filter_kwargs = {
                    "document_type": OrderDocumentTypeEnum.SHIPPING_LABEL
                }
            case "return_label":
                filter_kwargs = {
                    "document_type": OrderDocumentTypeEnum.RETURN_LABEL
                }
            case "credit_note":
                filter_kwargs = {
                    "document_type": OrderDocumentTypeEnum.CREDIT_NOTE
                }
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


class OrderValueFilter(RangeNumericListFilter):
    title = _("Order Value")
    parameter_name = "order_value"

    def queryset(self, request, queryset):
        return queryset

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


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


class OrderItemInline(TabularInline):
    model = OrderItem
    extra = 0
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
        if obj.product:
            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-base-900 dark:text-base-100">{name}</div>'
                '<div class="text-base-600 dark:text-base-300">ID: {id}</div>'
                "</div>",
                name=(
                    obj.product.safe_translation_getter(
                        "name", any_language=True
                    )
                    or "Unnamed Product"
                ),
                id=obj.product.id,
            )
        return "-"

    @admin.display(description=_("Unit Price"))
    def price_display(self, obj):
        return format_html(
            '<div class="text-sm font-medium text-base-900 dark:text-base-100">{price}</div>',
            price=str(obj.price),
        )

    @admin.display(description=_("Total"))
    def total_display(self, obj):
        return format_html(
            '<div class="text-sm font-bold text-base-900 dark:text-base-100">{total}</div>',
            total=str(obj.total_price),
        )

    @admin.display(description=_("Refund Status"))
    def refund_status(self, obj):
        if obj.is_refunded:
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "↩️ Refunded"
                "</span>"
            )
        if obj.refunded_quantity > 0:
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                "⚠️ Partial"
                "</span>"
            )
        return ""


class OrderHistoryInline(TabularInline):
    model = OrderHistory
    extra = 0
    max_num = 20
    fields = (
        "change_type",
        "description_display",
        "user_display",
        "created_at",
    )
    readonly_fields = (
        "change_type",
        "description_display",
        "user_display",
        "created_at",
    )
    ordering = ("-created_at",)

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
        description = (
            obj.safe_translation_getter("description", any_language=True)
            or "No description"
        )
        desc_display = (
            description[:100] + "..." if len(description) > 100 else description
        )
        return format_html(
            '<div class="text-sm text-base-700 dark:text-base-300">{desc}</div>',
            desc=desc_display,
        )

    @admin.display(description=_("Changed By"))
    def user_display(self, obj):
        if obj.user:
            return format_html(
                '<div class="text-sm text-base-700 dark:text-base-300">{name}</div>',
                name=obj.user.full_name or obj.user.username,
            )
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300">System</span>'
        )


def _invoice_download_url(invoice: Invoice) -> str | None:
    """Admin-only URL to stream the stored PDF back through admin auth."""
    if not invoice.pk:
        return None
    return reverse("admin:order_invoice_download", args=[invoice.pk])


class InvoiceInline(TabularInline):
    """Single-row inline surfacing the order's invoice (if any).

    Archival-only — the invoice itself is immutable once rendered
    (Greek tax law: no gaps, no edits). Admins regenerate or issue
    new invoices via the order-level detail actions on OrderAdmin.
    """

    model = Invoice
    extra = 0
    max_num = 0
    can_delete = False
    show_change_link = True
    tab = True

    fields = (
        "invoice_number",
        "issue_date",
        "total",
        "currency",
        "document_status",
    )
    readonly_fields = (
        "invoice_number",
        "issue_date",
        "total",
        "currency",
        "document_status",
    )

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Document"))
    def document_status(self, obj):
        if not obj or not obj.pk:
            return mark_safe(
                '<span class="text-base-600 dark:text-base-300 italic">—</span>'
            )
        if not obj.has_document():
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                "⏳ Pending render"
                "</span>"
            )
        return format_html(
            '<a href="{url}" target="_blank" rel="noopener" '
            'class="inline-flex items-center px-2 py-1 text-xs font-medium '
            "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 "
            'rounded-full hover:underline">'
            "📄 Download PDF"
            "</a>",
            url=_invoice_download_url(obj) or "",
        )


@admin.register(Order)
class OrderAdmin(BaseModelAdmin):
    list_display = [
        "status_badge",
        "customer_info",
        "order_summary",
        "payment_info",
        "currency_status",
        "document_type_badge",
        "shipping_info",
        "created_display",
        "urgency_indicator",
    ]
    list_filter = [
        OrderStatusGroupFilter,
        PaymentStatusFilter,
        DocumentTypeFilter,
        RecentOrdersFilter,
        "status",
        "payment_status",
        ("created_at", RangeDateTimeFilter),
        ("status_updated_at", RangeDateTimeFilter),
        ("country", RelatedDropdownFilter),
        ("region", RelatedDropdownFilter),
        ("pay_way", RelatedDropdownFilter),
        "payment_method",
        "document_type",
        # Filter Orders by carrier — replaces the legacy
        # ``shipping_method`` enum filter; FK lookup is denser
        # (provider name) but supports the same support-ticket
        # workflow.
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
        "created_at",
        "updated_at",
        "status_updated_at",
        "order_analytics",
        "financial_summary",
        "customer_summary",
        "shipping_summary",
        # ``boxnow_summary`` is a computed display that surfaces the
        # BoxNow parcel state inline in the Shipping fieldset so admins
        # don't have to scroll to the inline below.
        "boxnow_summary",
    )

    fieldsets = (
        (
            _("Order Information"),
            {
                "fields": (
                    "uuid",
                    "status",
                    "document_type",
                    "created_at",
                    "updated_at",
                    "status_updated_at",
                ),
                "classes": ("tab",),
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
                    "customer_summary",
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
                    "shipping_summary",
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
                    "paid_amount",
                    "financial_summary",
                ),
                "classes": ("tab",),
                "description": _(
                    "⚠️ Note: Ensure all money fields use the same currency (EUR preferred) to avoid calculation errors."
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
                    "boxnow_summary",
                ),
                "classes": ("tab",),
                "description": _(
                    "💡 Shipping price currency should match item currencies to avoid total calculation errors."
                ),
            },
        ),
        (
            _("Additional Information"),
            {
                "fields": (
                    "customer_notes",
                    "order_analytics",
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
        "mark_as_canceled",
    ]
    # Detail header had 5 long English buttons that overflowed the
    # title bar. Group invoice + myDATA ops under one dropdown, keep
    # the shipping-voucher button top-level (it's the most-used
    # one-click op and reads cleaner with a short label).
    actions_detail = [
        "download_shipping_voucher",
        {
            "title": _("Invoicing & myDATA"),
            "icon": "receipt_long",
            "items": [
                "generate_invoice_now",
                "regenerate_invoice",
                "send_invoice_to_mydata_now",
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
    inlines = [OrderItemInline, InvoiceInline, OrderHistoryInline]
    date_hierarchy = "created_at"
    list_select_related = ["user", "country", "region", "pay_way"]

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
        # Registry-driven inline routing — the legacy
        # ``shipping_method`` enum no longer informs which inline to
        # mount. Pre-Phase-0 rows (no ``shipping_provider`` set) get
        # neither carrier inline; their data still lives on the order
        # itself and can be reviewed from the change form fields.
        if provider_code == "boxnow":
            from shipping_boxnow.admin import (  # noqa: PLC0415
                BoxNowShipmentOrderInline,
            )

            inlines.append(BoxNowShipmentOrderInline)
        elif provider_code == "acs":
            from shipping_acs.admin import (  # noqa: PLC0415
                AcsShipmentOrderInline,
            )

            inlines.append(AcsShipmentOrderInline)
        return inlines

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                item_count=Count("items"),
                total_items_quantity=Sum("items__quantity"),
            )
            .select_related("user", "country", "region", "pay_way")
        )

    @admin.display(description=_("Customer"))
    def customer_info(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{name}</div>'
            '<div class="text-base-600 dark:text-base-300">{email}</div>'
            '<div class="text-base-600 dark:text-base-300">{phone}</div>'
            "</div>",
            name=f"{obj.first_name} {obj.last_name}",
            email=obj.email,
            phone=obj.phone or "No phone",
        )

    @admin.display(description=_("Order Summary"))
    def order_summary(self, obj):
        item_count = getattr(obj, "item_count", 0)
        total_qty = getattr(obj, "total_items_quantity", 0)

        try:
            price_display = format_html("{}", str(obj.total_price))
        except ValueError as e:
            price_display = format_html(
                '<span class="text-red-600 dark:text-red-400" title="Currency mismatch: {error}">'
                "Items: {items} + Ship: {shipping}"
                "</span>",
                error=str(e),
                items=str(obj.total_price_items),
                shipping=str(obj.total_price_extra),
            )

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{count} items</div>'
            '<div class="text-base-600 dark:text-base-300">Qty: {qty}</div>'
            '<div class="font-bold text-base-900 dark:text-base-100">{price}</div>'
            "</div>",
            count=item_count,
            qty=total_qty or 0,
            price=price_display,
        )

    @admin.display(description=_("Payment"))
    def payment_info(self, obj):
        return format_html(
            '<div class="text-sm">'
            "<div>{badge}</div>"
            '<div class="font-medium text-base-900 dark:text-base-100">{amount}</div>'
            '<div class="text-base-600 dark:text-base-300">{method}</div>'
            "</div>",
            badge=self.payment_status_badge(obj),
            amount=str(obj.paid_amount or obj.total_price),
            method=obj.payment_method or "Not set",
        )

    @admin.display(description=_("Shipment Summary"))
    def boxnow_summary(self, obj):
        """Compact summary of the carrier shipment, shown inline in
        the Shipping & Tracking fieldset.

        Field name kept as ``boxnow_summary`` for backwards-compat
        with existing fieldsets/readonly references — display label
        is generic. For ACS orders we render the ACS state + voucher;
        for BoxNow we keep the original badge + locker rendering.
        """
        provider_code = (
            obj.shipping_provider.code if obj.shipping_provider_id else None
        )
        is_acs = provider_code == "acs"
        is_boxnow = provider_code == "boxnow"

        if is_acs:
            return self._acs_summary_html(obj)
        if not is_boxnow:
            return format_html(
                '<span class="text-sm text-base-500">{}</span>',
                _("No carrier-specific shipment for this order."),
            )

        shipment = getattr(obj, "boxnow_shipment", None)
        if shipment is None:
            return format_html(
                '<span class="text-sm text-orange-600">{}</span>',
                _(
                    "shipping_provider=boxnow but no BoxNowShipment "
                    "row — order created before the BoxNow integration "
                    "shipped, or a service-layer bug. Inspect "
                    "order.history for clues."
                ),
            )

        # Map parcel_state → Tailwind colour for the badge.
        state_color = {
            "pending_creation": "bg-base-200 text-base-700",
            "new": "bg-blue-100 text-blue-700",
            "in_depot": "bg-cyan-100 text-cyan-700",
            "final_destination": "bg-amber-100 text-amber-700",
            "delivered": "bg-green-100 text-green-700",
            "returned": "bg-red-100 text-red-700",
            "expired": "bg-red-100 text-red-700",
            "canceled": "bg-red-100 text-red-700",
            "missing": "bg-red-100 text-red-700",
            "lost": "bg-red-100 text-red-700",
            "accepted_for_return": "bg-cyan-100 text-cyan-700",
            "accepted_to_locker": "bg-blue-100 text-blue-700",
        }.get(shipment.parcel_state, "bg-base-200 text-base-700")

        if shipment.parcel_id:
            voucher = format_html(
                '<div class="font-mono text-sm">{}</div>',
                shipment.parcel_id,
            )
        else:
            voucher = format_html(
                '<div class="text-sm text-orange-600">{}</div>',
                _("Voucher pending — fires after payment"),
            )

        if shipment.locker is not None:
            locker_name = format_html(" &middot; {}", shipment.locker.name)
        else:
            locker_name = ""

        return format_html(
            '<div class="space-y-1 text-sm">'
            '<div><span class="rounded px-2 py-0.5 text-xs font-medium {color}">{label}</span></div>'
            "<div><strong>{voucher_label}:</strong> {voucher}</div>"
            "<div><strong>{locker_label}:</strong> "
            '<span class="font-mono">{locker_id}</span>{locker_name}</div>'
            "</div>",
            color=state_color,
            label=shipment.get_parcel_state_display(),
            voucher_label=_("Voucher"),
            voucher=voucher,
            locker_label=_("Locker"),
            locker_id=shipment.locker_external_id or "—",
            locker_name=locker_name,
        )

    def _acs_summary_html(self, obj):
        """ACS shipment summary helper used by ``boxnow_summary``.

        Kept as a private helper rather than its own admin display so
        the existing fieldset doesn't need a second column added — the
        single ``boxnow_summary`` field renders whichever carrier is
        attached. Mirrors the BoxNow rendering for visual consistency.
        """
        shipment = getattr(obj, "acs_shipment", None)
        if shipment is None:
            return format_html(
                '<span class="text-sm text-orange-600">{}</span>',
                _(
                    "ACS order without an AcsShipment row — likely a "
                    "race during order creation or an upgrade-time "
                    "data gap. Inspect order.history for clues."
                ),
            )

        state_color = {
            "pending_creation": "bg-base-200 text-base-700",
            "new": "bg-blue-100 text-blue-700",
            "in_transit": "bg-cyan-100 text-cyan-700",
            "at_destination": "bg-amber-100 text-amber-700",
            "out_for_delivery": "bg-amber-100 text-amber-700",
            "delivered": "bg-green-100 text-green-700",
            "attempted": "bg-orange-100 text-orange-700",
            "returned": "bg-red-100 text-red-700",
            "canceled": "bg-red-100 text-red-700",
            "lost": "bg-red-100 text-red-700",
        }.get(shipment.shipment_state, "bg-base-200 text-base-700")

        if shipment.voucher_no:
            voucher_html = format_html(
                '<div class="font-mono text-sm">{}</div>',
                shipment.voucher_no,
            )
        else:
            voucher_html = format_html(
                '<div class="text-sm text-orange-600">{}</div>',
                _("Voucher pending — fires after order creation"),
            )

        rows = [
            format_html(
                '<div><span class="rounded px-2 py-0.5 text-xs font-medium {color}">{label}</span></div>',
                color=state_color,
                label=shipment.get_shipment_state_display(),
            ),
            format_html(
                "<div><strong>{lbl}:</strong> {voucher}</div>",
                lbl=_("Voucher"),
                voucher=voucher_html,
            ),
            format_html(
                "<div><strong>{lbl}:</strong> {kind}</div>",
                lbl=_("Kind"),
                kind=shipment.get_delivery_kind_display(),
            ),
        ]

        if shipment.charge_type == 2 and shipment.cod_amount:  # COD
            rows.append(
                format_html(
                    "<div><strong>{lbl}:</strong> "
                    '<span class="font-mono">{amount}</span></div>',
                    lbl=_("COD"),
                    amount=str(shipment.cod_amount),
                )
            )

        if shipment.station_destination_external_id:
            rows.append(
                format_html(
                    "<div><strong>{lbl}:</strong> "
                    '<span class="font-mono">{station}</span></div>',
                    lbl=_("Smartpoint"),
                    station=shipment.station_destination_external_id,
                )
            )

        return format_html(
            '<div class="space-y-1 text-sm">{rows}</div>',
            rows=format_html_join("", "{}", ((r,) for r in rows)),
        )

    @admin.display(description=_("Shipping"))
    def shipping_info(self, obj):
        if obj.tracking_number:
            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-blue-600 dark:text-blue-400">{tracking}</div>'
                '<div class="text-base-600 dark:text-base-300">{carrier}</div>'
                '<div class="text-base-600 dark:text-base-300">{city}</div>'
                "</div>",
                tracking=obj.tracking_number,
                carrier=obj.shipping_carrier or "Unknown carrier",
                city=obj.city,
            )
        return format_html(
            '<div class="text-sm">'
            '<div class="text-base-600 dark:text-base-300">No tracking</div>'
            '<div class="text-base-600 dark:text-base-300">{city}</div>'
            "</div>",
            city=obj.city,
        )

    @admin.display(description=_("Created"))
    def created_display(self, obj):
        now = timezone.now()
        diff = now - obj.created_at

        if diff < timedelta(hours=1):
            time_ago = f"{diff.seconds // 60}m ago"
            color = "text-green-600 dark:text-green-400"
        elif diff < timedelta(days=1):
            time_ago = f"{diff.seconds // 3600}h ago"
            color = "text-blue-600 dark:text-blue-400"
        else:
            time_ago = f"{diff.days}d ago"
            color = "text-base-600 dark:text-base-400"

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{date}</div>'
            '<div class="{color}">{time}</div>'
            "</div>",
            date=obj.created_at.strftime("%Y-%m-%d %H:%M"),
            color=color,
            time=time_ago,
        )

    @admin.display(description=_("Priority"))
    def urgency_indicator(self, obj):
        if not obj.created_at:
            return "Available after creation."

        now = timezone.now()
        age = now - obj.created_at

        if obj.status == OrderStatus.PENDING and age > timedelta(hours=24):
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "🚨 Urgent"
                "</span>"
            )
        if obj.status == OrderStatus.PROCESSING and age > timedelta(days=3):
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                "⚠️ Delayed"
                "</span>"
            )
        if obj.status == OrderStatus.SHIPPED and age > timedelta(days=7):
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded-full">'
                "📦 Follow up"
                "</span>"
            )
        return ""

    @admin.display(description=_("Status"))
    def status_badge(self, obj):
        status_config = {
            OrderStatus.PENDING: {
                "bg": "bg-orange-50 dark:bg-orange-900",
                "text": "text-orange-700 dark:text-orange-300",
                "icon": "⏳",
            },
            OrderStatus.PROCESSING: {
                "bg": "bg-blue-50 dark:bg-blue-900",
                "text": "text-blue-700 dark:text-blue-300",
                "icon": "⚙️",
            },
            OrderStatus.SHIPPED: {
                "bg": "bg-purple-50 dark:bg-purple-900",
                "text": "text-purple-700 dark:text-purple-300",
                "icon": "🚚",
            },
            OrderStatus.DELIVERED: {
                "bg": "bg-green-50 dark:bg-green-900",
                "text": "text-green-700 dark:text-green-300",
                "icon": "📦",
            },
            OrderStatus.COMPLETED: {
                "bg": "bg-emerald-50 dark:bg-emerald-900",
                "text": "text-emerald-700 dark:text-emerald-300",
                "icon": "✅",
            },
            OrderStatus.CANCELED: {
                "bg": "bg-red-50 dark:bg-red-900",
                "text": "text-red-700 dark:text-red-300",
                "icon": "❌",
            },
            OrderStatus.RETURNED: {
                "bg": "bg-yellow-50 dark:bg-yellow-900",
                "text": "text-yellow-700 dark:text-yellow-300",
                "icon": "↩️",
            },
            OrderStatus.REFUNDED: {
                "bg": "bg-gray-50 dark:bg-gray-900",
                "text": "text-base-700 dark:text-base-300",
                "icon": "💰",
            },
        }

        config = status_config.get(
            obj.status,
            {
                "bg": "bg-gray-50 dark:bg-gray-900",
                "text": "text-base-700 dark:text-base-300",
                "icon": "❓",
            },
        )

        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            '{bg} {text_class} rounded-full gap-1">'
            "<span>{icon}</span>"
            "<span>{label}</span>"
            "</span>",
            bg=config["bg"],
            text_class=config["text"],
            icon=config["icon"],
            label=obj.get_status_display(),
        )

    def payment_status_badge(self, obj):
        payment_config = {
            PaymentStatus.COMPLETED: {
                "bg": "bg-green-50 dark:bg-green-900",
                "text": "text-green-700 dark:text-green-300",
                "icon": "✅",
            },
            PaymentStatus.PENDING: {
                "bg": "bg-orange-50 dark:bg-orange-900",
                "text": "text-orange-700 dark:text-orange-300",
                "icon": "⏳",
            },
            PaymentStatus.PROCESSING: {
                "bg": "bg-blue-50 dark:bg-blue-900",
                "text": "text-blue-700 dark:text-blue-300",
                "icon": "⚙️",
            },
            PaymentStatus.FAILED: {
                "bg": "bg-red-50 dark:bg-red-900",
                "text": "text-red-700 dark:text-red-300",
                "icon": "❌",
            },
            PaymentStatus.REFUNDED: {
                "bg": "bg-purple-50 dark:bg-purple-900",
                "text": "text-purple-700 dark:text-purple-300",
                "icon": "↩️",
            },
            PaymentStatus.PARTIALLY_REFUNDED: {
                "bg": "bg-yellow-50 dark:bg-yellow-900",
                "text": "text-yellow-700 dark:text-yellow-300",
                "icon": "⚠️",
            },
            PaymentStatus.CANCELED: {
                "bg": "bg-gray-50 dark:bg-gray-900",
                "text": "text-base-700 dark:text-base-700",
                "icon": "🚫",
            },
        }

        config = payment_config.get(
            obj.payment_status,
            {
                "bg": "bg-gray-50 dark:bg-gray-900",
                "text": "text-base-700 dark:text-base-700",
                "icon": "❓",
            },
        )

        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            '{bg} {text_class} rounded-full gap-1">'
            "<span>{icon}</span>"
            "<span>{label}</span>"
            "</span>",
            bg=config["bg"],
            text_class=config["text"],
            icon=config["icon"],
            label=obj.get_payment_status_display(),
        )

    @admin.display(description=_("Document Type"))
    def document_type_badge(self, obj):
        document_config = {
            OrderDocumentTypeEnum.RECEIPT: {
                "bg": "bg-blue-50 dark:bg-blue-900",
                "text": "text-blue-700 dark:text-blue-300",
                "icon": "🧾",
            },
            OrderDocumentTypeEnum.INVOICE: {
                "bg": "bg-green-50 dark:bg-green-900",
                "text": "text-green-700 dark:text-green-300",
                "icon": "📄",
            },
            OrderDocumentTypeEnum.PROFORMA: {
                "bg": "bg-orange-50 dark:bg-orange-900",
                "text": "text-orange-700 dark:text-orange-300",
                "icon": "📋",
            },
            OrderDocumentTypeEnum.SHIPPING_LABEL: {
                "bg": "bg-purple-50 dark:bg-purple-900",
                "text": "text-purple-700 dark:text-purple-300",
                "icon": "🏷️",
            },
            OrderDocumentTypeEnum.RETURN_LABEL: {
                "bg": "bg-red-50 dark:bg-red-900",
                "text": "text-red-700 dark:text-red-300",
                "icon": "↩️",
            },
            OrderDocumentTypeEnum.CREDIT_NOTE: {
                "bg": "bg-yellow-50 dark:bg-yellow-900",
                "text": "text-yellow-700 dark:text-yellow-300",
                "icon": "💳",
            },
        }

        config = document_config.get(
            obj.document_type,
            {
                "bg": "bg-gray-50 dark:bg-gray-900",
                "text": "text-base-700 dark:text-base-700",
                "icon": "📄",
            },
        )

        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            '{bg} {text_class} rounded-full gap-1">'
            "<span>{icon}</span>"
            "<span>{label}</span>"
            "</span>",
            bg=config["bg"],
            text_class=config["text"],
            icon=config["icon"],
            label=obj.get_document_type_display(),
        )

    @admin.display(description=_("Currency"))
    def currency_status(self, obj):
        try:
            items_currency = obj.total_price_items.currency
            shipping_currency = obj.shipping_price.currency

            if items_currency == shipping_currency:
                return format_html(
                    '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                    'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                    "✅ {currency}"
                    "</span>",
                    currency=str(items_currency),
                )
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "⚠️ Mixed"
                "</span>"
            )
        except ValueError:
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "❌ Mismatch"
                "</span>"
            )

    @admin.display(description=_("Customer Summary"))
    def customer_summary(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Full Name:</strong></div><div>{name}</div>"
            "<div><strong>Email:</strong></div><div>{email}</div>"
            "<div><strong>Phone:</strong></div><div>{phone}</div>"
            "<div><strong>Account:</strong></div><div>{account}</div>"
            "</div>"
            "</div>",
            name=obj.customer_full_name,
            email=obj.email,
            phone=obj.phone or "Not provided",
            account="Registered User" if obj.user else "Guest",
        )

    @admin.display(description=_("Financial Summary"))
    def financial_summary(self, obj):
        try:
            total_display = format_html("{}", str(obj.total_price))
            currency_warning = mark_safe("")
        except ValueError as e:
            total_display = mark_safe(
                '<span class="text-red-600 dark:text-red-400">Currency Mismatch</span>'
            )
            currency_warning = format_html(
                "<div><strong>Currency Issue:</strong></div>"
                '<div class="text-red-600 dark:text-red-400 text-xs">{err}</div>',
                err=str(e),
            )

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Items Total:</strong></div><div>{items}</div>"
            "<div><strong>Shipping:</strong></div><div>{shipping}</div>"
            '<div><strong>Grand Total:</strong></div><div class="font-bold">{total}</div>'
            "<div><strong>Paid Amount:</strong></div><div>{paid}</div>"
            "<div><strong>Payment Status:</strong></div><div>{pstatus}</div>"
            "<div><strong>Payment Method:</strong></div><div>{pmethod}</div>"
            "<div><strong>Document Type:</strong></div><div>{doc}</div>"
            "{warning}"
            "</div>"
            "</div>",
            items=str(obj.total_price_items),
            shipping=str(obj.shipping_price),
            total=total_display,
            paid=str(obj.paid_amount or "Not paid"),
            pstatus=obj.get_payment_status_display(),
            pmethod=obj.payment_method or "Not specified",
            doc=obj.get_document_type_display(),
            warning=currency_warning,
        )

    @admin.display(description=_("Shipping Summary"))
    def shipping_summary(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Full Address:</strong></div><div>{address}</div>"
            "<div><strong>Tracking:</strong></div><div>{tracking}</div>"
            "<div><strong>Carrier:</strong></div><div>{carrier}</div>"
            "<div><strong>Shipping Cost:</strong></div><div>{price}</div>"
            "</div>"
            "</div>",
            address=obj.full_address,
            tracking=obj.tracking_number or "Not assigned",
            carrier=obj.shipping_carrier or "Not assigned",
            price=str(obj.shipping_price),
        )

    @admin.display(description=_("Order Analytics"))
    def order_analytics(self, obj):
        if not obj.created_at:
            return "Available after creation."

        now = timezone.now()
        age = now - obj.created_at

        processing_time = ""
        if obj.status_updated_at:
            status_age = now - obj.status_updated_at
            processing_time = (
                f"{status_age.days}d {status_age.seconds // 3600}h"
            )

        available_docs = OrderDocumentTypeEnum.get_document_types_for_status(
            obj.status
        )
        doc_types = ", ".join([str(doc.label) for doc in available_docs])

        currency_status = "OK"
        try:
            items_currency = obj.total_price_items.currency
            shipping_currency = obj.shipping_price.currency
            if items_currency != shipping_currency:
                currency_status = f"Mixed: {items_currency}/{shipping_currency}"
        except ValueError:
            currency_status = "Mismatch Error"

        currency_class = (
            "text-red-600 dark:text-red-400"
            if "Mismatch" in currency_status or "Mixed" in currency_status
            else ""
        )

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Order Age:</strong></div><div>{days}d {hours}h</div>"
            "<div><strong>Status Age:</strong></div><div>{processing}</div>"
            "<div><strong>Items Count:</strong></div><div>{count}</div>"
            "<div><strong>Can Cancel:</strong></div><div>{can_cancel}</div>"
            "<div><strong>Is Paid:</strong></div><div>{is_paid}</div>"
            '<div><strong>Currency Status:</strong></div><div class="{cls}">{currency}</div>'
            '<div><strong>Available Docs:</strong></div><div class="text-xs">{docs}</div>'
            "</div>"
            "</div>",
            days=age.days,
            hours=age.seconds // 3600,
            processing=processing_time or "N/A",
            count=getattr(obj, "item_count", 0),
            can_cancel="Yes" if obj.can_be_canceled else "No",
            is_paid="Yes" if obj.is_paid else "No",
            cls=currency_class,
            currency=currency_status,
            docs=doc_types,
        )

    @action(
        description=str(_("Mark selected orders as processing")),
        variant=ActionVariant.PRIMARY,
        icon="play_arrow",
    )
    def mark_as_processing(self, request, queryset):
        with transaction.atomic():
            for order in queryset:
                try:
                    OrderService.update_order_status(
                        order, OrderStatus.PROCESSING
                    )
                    self.message_user(
                        request,
                        _("Order %(order_id)s marked as processing")
                        % {"order_id": order.id},
                    )
                except ValueError as e:
                    self.message_user(request, f"Error: {e!s}", level="error")

    @action(
        description=str(_("Mark selected orders as shipped")),
        variant=ActionVariant.INFO,
        icon="local_shipping",
    )
    def mark_as_shipped(self, request, queryset):
        with transaction.atomic():
            for order in queryset:
                try:
                    OrderService.update_order_status(order, OrderStatus.SHIPPED)
                    self.message_user(
                        request,
                        _("Order %(order_id)s marked as shipped")
                        % {"order_id": order.id},
                    )
                except ValueError as e:
                    self.message_user(request, f"Error: {e!s}", level="error")

    @action(
        description=str(_("Mark selected orders as delivered")),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def mark_as_delivered(self, request, queryset):
        with transaction.atomic():
            for order in queryset:
                try:
                    OrderService.update_order_status(
                        order, OrderStatus.DELIVERED
                    )
                    self.message_user(
                        request,
                        _("Order %(order_id)s marked as delivered")
                        % {"order_id": order.id},
                    )
                except ValueError as e:
                    self.message_user(request, f"Error: {e!s}", level="error")

    @action(
        description=str(_("Mark selected orders as completed")),
        variant=ActionVariant.SUCCESS,
        icon="task_alt",
    )
    def mark_as_completed(self, request, queryset):
        with transaction.atomic():
            for order in queryset:
                try:
                    OrderService.update_order_status(
                        order, OrderStatus.COMPLETED
                    )
                    self.message_user(
                        request,
                        _("Order %(order_id)s marked as completed")
                        % {"order_id": order.id},
                    )
                except ValueError as e:
                    self.message_user(request, f"Error: {e!s}", level="error")

    @action(
        description=str(_("Cancel selected orders and restore stock")),
        variant=ActionVariant.DANGER,
        icon="cancel",
    )
    def mark_as_canceled(self, request, queryset):
        with transaction.atomic():
            for order in queryset:
                try:
                    OrderService.cancel_order(order)
                    self.message_user(
                        request,
                        _("Order %(order_id)s marked as canceled")
                        % {"order_id": order.id},
                    )
                except ValueError as e:
                    self.message_user(request, f"Error: {e!s}", level="error")

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
            raise Http404(_("Invoice not found.")) from exc
        if not invoice.has_document():
            raise Http404(_("Invoice PDF has not been generated yet."))
        return FileResponse(
            invoice.document_file.open("rb"),
            content_type="application/pdf",
            as_attachment=True,
            filename=f"{invoice.invoice_number}.pdf",
        )

    def _redirect_to_order_change(self, object_id):
        return redirect(reverse("admin:order_order_change", args=[object_id]))

    @action(
        description=str(_("Generate invoice")),
        variant=ActionVariant.PRIMARY,
        icon="receipt_long",
    )
    def generate_invoice_now(self, request, object_id):
        """Synchronously render an invoice for this order.

        Idempotent — if an invoice already exists it's returned as-is
        (no counter slot consumed, no file re-render). Runs inline
        rather than via Celery so the admin gets immediate success /
        failure feedback.
        """
        try:
            order = Order.objects.get(pk=object_id)
        except Order.DoesNotExist:
            messages.error(request, _("Order not found."))
            return self._redirect_to_order_change(object_id)

        if order.document_type != OrderDocumentTypeEnum.INVOICE.value:
            messages.warning(
                request,
                _(
                    "Order #%(order_id)s has document_type=%(doc)s, not "
                    "INVOICE. Generating anyway as an explicit admin "
                    "override — update the order's document type if this "
                    "is ongoing."
                )
                % {"order_id": order.id, "doc": order.document_type},
            )

        try:
            invoice = generate_invoice(order)
        except Exception as exc:  # noqa: BLE001 — surface all errors in admin
            logger.exception(
                "Admin invoice generation failed for order %s", order.id
            )
            messages.error(
                request,
                _("Invoice generation failed: %(err)s") % {"err": str(exc)},
            )
            return self._redirect_to_order_change(object_id)

        if invoice.has_document():
            messages.success(
                request,
                _("Invoice %(num)s ready for order #%(order_id)s.")
                % {"num": invoice.invoice_number, "order_id": order.id},
            )
        else:
            messages.warning(
                request,
                _(
                    "Invoice row %(num)s exists but the PDF was not "
                    "rendered — check server logs."
                )
                % {"num": invoice.invoice_number},
            )
        return self._redirect_to_order_change(object_id)

    @action(
        description=str(_("Regenerate invoice (same number)")),
        variant=ActionVariant.WARNING,
        icon="refresh",
    )
    def regenerate_invoice(self, request, object_id):
        """Re-render the invoice PDF + snapshots in place.

        Preserves the original ``invoice_number`` and ``issue_date`` so
        the sequential register stays gap-free (Greek tax law). Only
        the PDF, seller / buyer snapshots, and derived totals are
        refreshed — use this to fix a corrupted PDF or to apply an
        updated seller address/VAT ID without breaking audit traceability.
        """
        try:
            order = Order.objects.get(pk=object_id)
        except Order.DoesNotExist:
            messages.error(request, _("Order not found."))
            return self._redirect_to_order_change(object_id)

        try:
            invoice = generate_invoice(order, force=True)
        except Exception as exc:  # noqa: BLE001
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
        description=str(_("Send invoice to myDATA")),
        variant=ActionVariant.PRIMARY,
        icon="cloud_upload",
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

        from order.mydata.config import load_config
        from order.tasks import send_invoice_to_mydata

        if not load_config().is_ready():
            messages.warning(
                request,
                _(
                    "myDATA integration is not ready — check MYDATA_ENABLED "
                    "and credentials in Settings before retrying."
                ),
            )
            return self._redirect_to_order_change(object_id)

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
        description=str(_("Cancel invoice in myDATA")),
        variant=ActionVariant.DANGER,
        icon="cancel",
    )
    def cancel_mydata_invoice_now(self, request, object_id):
        """Schedule a ``CancelInvoice`` call for this order's MARK.

        Only valid when the invoice has already been registered. The
        Greek tax-law cancellation window is tight (same accounting
        period) — beyond it, issue a credit note (5.1 / 11.4) instead.
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
        description=str(_("Download shipping voucher (PDF)")),
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
        from django.http import HttpResponse  # noqa: PLC0415

        from shipping.services import ShippingService  # noqa: PLC0415

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

        try:
            pdf_bytes = adapter.fetch_label_bytes(shipment)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Admin download_shipping_voucher failed for order %s",
                order.id,
            )
            messages.error(
                request,
                _("Failed to fetch voucher: %(err)s") % {"err": str(exc)},
            )
            return self._redirect_to_order_change(object_id)

        identifier = (
            getattr(shipment, "voucher_no", None)
            or getattr(shipment, "parcel_id", None)
            or order.id
        )
        filename = f"{adapter.code}-voucher-{identifier}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Content-Length"] = str(len(pdf_bytes))
        return response

    @action(
        description=str(_("Customer's orders")),
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
        "product_display",
        "quantity_display",
        "pricing_info",
        "refund_status_display",
        "created_at",
    ]
    list_filter = [
        "order__status",
        "order__payment_status",
        ("product", RelatedDropdownFilter),
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
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "total_price",
        "refunded_amount",
        "net_price",
        "item_analytics",
    ]
    list_select_related = ["order", "order__user", "product"]

    fieldsets = (
        (
            _("Order Item Information"),
            {
                "fields": ("order", "product", "quantity", "price"),
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
                "fields": ("total_price", "refunded_amount", "net_price"),
                "classes": ("collapse",),
            },
        ),
        (
            _("Additional Information"),
            {
                "fields": ("notes", "item_analytics"),
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

    @admin.display(description=_("Order"))
    def order_link(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<a href="{url}" class="font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300">Order #{id}</a>'
            "<div>{badge}</div>"
            "</div>",
            url=f"/admin/order/order/{obj.order.id}/change/",
            id=obj.order.id,
            badge=self.order_status_mini(obj.order),
        )

    def order_status_mini(self, order):
        status_colors = {
            OrderStatus.PENDING: "text-orange-600 dark:text-orange-400",
            OrderStatus.PROCESSING: "text-blue-600 dark:text-blue-400",
            OrderStatus.SHIPPED: "text-purple-600 dark:text-purple-400",
            OrderStatus.DELIVERED: "text-green-600 dark:text-green-400",
            OrderStatus.COMPLETED: "text-emerald-600 dark:text-emerald-400",
            OrderStatus.CANCELED: "text-red-600 dark:text-red-400",
            OrderStatus.RETURNED: "text-yellow-600 dark:text-yellow-400",
            OrderStatus.REFUNDED: "text-base-600 dark:text-base-400",
        }
        color = status_colors.get(
            order.status, "text-base-600 dark:text-base-400"
        )
        return format_html(
            '<span class="{color}">{status}</span>',
            color=color,
            status=order.get_status_display(),
        )

    @admin.display(description=_("Product"))
    def product_display(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{name}</div>'
            '<div class="text-base-600 dark:text-base-300">ID: {id}</div>'
            "</div>",
            name=(
                obj.product.safe_translation_getter("name", any_language=True)
                or "Unnamed Product"
            ),
            id=obj.product.id,
        )

    @admin.display(description=_("Quantity"))
    def quantity_display(self, obj):
        if obj.refunded_quantity > 0:
            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-base-900 dark:text-base-100">Total: {qty}</div>'
                '<div class="text-red-600 dark:text-red-400">Refunded: {refunded}</div>'
                '<div class="text-green-600 dark:text-green-400">Net: {net}</div>'
                "</div>",
                qty=obj.quantity,
                refunded=obj.refunded_quantity,
                net=obj.net_quantity,
            )
        return format_html(
            '<span class="inline-flex items-center px-3 py-1 text-sm font-medium '
            'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">'
            "x{qty}"
            "</span>",
            qty=obj.quantity,
        )

    @admin.display(description=_("Pricing"))
    def pricing_info(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{price} each</div>'
            '<div class="font-bold text-base-900 dark:text-base-100">Total: {total}</div>'
            '<div class="text-green-600 dark:text-green-400">Net: {net}</div>'
            "</div>",
            price=str(obj.price),
            total=str(obj.total_price),
            net=str(obj.net_price),
        )

    @admin.display(description=_("Refund Status"))
    def refund_status_display(self, obj):
        if obj.is_refunded:
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "↩️ Fully Refunded"
                "</span>"
            )
        if obj.refunded_quantity > 0:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                "⚠️ Partial ({refunded}/{qty})"
                "</span>",
                refunded=obj.refunded_quantity,
                qty=obj.quantity,
            )
        return mark_safe(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
            "✅ Active"
            "</span>"
        )

    @admin.display(description=_("Item Analytics"))
    def item_analytics(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Original Qty:</strong></div><div>{orig}</div>"
            "<div><strong>Current Qty:</strong></div><div>{current}</div>"
            "<div><strong>Refunded Qty:</strong></div><div>{refunded}</div>"
            "<div><strong>Net Qty:</strong></div><div>{net_qty}</div>"
            "<div><strong>Refunded Amount:</strong></div><div>{refunded_amt}</div>"
            "<div><strong>Net Amount:</strong></div><div>{net_price}</div>"
            "</div>"
            "</div>",
            orig=str(obj.original_quantity or obj.quantity),
            current=obj.quantity,
            refunded=obj.refunded_quantity,
            net_qty=obj.net_quantity,
            refunded_amt=str(obj.refunded_amount),
            net_price=str(obj.net_price),
        )


@admin.register(OrderHistory)
class OrderHistoryAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
    list_display = [
        "order_link",
        "change_type_badge",
        "description_display",
        "user_display",
        "ip_address",
        "created_at",
    ]
    list_filter = [
        "change_type",
        ("order", RelatedDropdownFilter),
        ("user", RelatedDropdownFilter),
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

    @admin.display(description=_("Order"))
    def order_link(self, obj):
        return format_html(
            '<a href="{url}" class="font-medium text-blue-600 dark:text-blue-400 '
            'hover:text-blue-800 dark:hover:text-blue-300">Order #{id}</a>',
            url=f"/admin/order/order/{obj.order.id}/change/",
            id=obj.order.id,
        )

    @admin.display(description=_("Change Type"))
    def change_type_badge(self, obj):
        type_config = {
            "STATUS": {
                "bg": "bg-blue-50 dark:bg-blue-900",
                "text": "text-blue-700 dark:text-blue-300",
                "icon": "📊",
            },
            "PAYMENT": {
                "bg": "bg-green-50 dark:bg-green-900",
                "text": "text-green-700 dark:text-green-300",
                "icon": "💳",
            },
            "SHIPPING": {
                "bg": "bg-purple-50 dark:bg-purple-900",
                "text": "text-purple-700 dark:text-purple-300",
                "icon": "🚚",
            },
            "CUSTOMER": {
                "bg": "bg-orange-50 dark:bg-orange-900",
                "text": "text-orange-700 dark:text-orange-300",
                "icon": "👤",
            },
            "ITEMS": {
                "bg": "bg-red-50 dark:bg-red-900",
                "text": "text-red-700 dark:text-red-300",
                "icon": "📦",
            },
            "ADDRESS": {
                "bg": "bg-yellow-50 dark:bg-yellow-900",
                "text": "text-yellow-700 dark:text-yellow-300",
                "icon": "📍",
            },
            "NOTE": {
                "bg": "bg-indigo-50 dark:bg-indigo-900",
                "text": "text-indigo-700 dark:text-indigo-300",
                "icon": "📝",
            },
            "REFUND": {
                "bg": "bg-gray-50 dark:bg-gray-900",
                "text": "text-base-700 dark:text-base-700",
                "icon": "💰",
            },
        }

        config = type_config.get(
            obj.change_type,
            {
                "bg": "bg-gray-50 dark:bg-gray-900",
                "text": "text-base-700 dark:text-base-700",
                "icon": "📋",
            },
        )

        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            '{bg} {text_class} rounded-full gap-1">'
            "<span>{icon}</span>"
            "<span>{label}</span>"
            "</span>",
            bg=config["bg"],
            text_class=config["text"],
            icon=config["icon"],
            label=obj.get_change_type_display(),
        )

    @admin.display(description=_("Description"))
    def description_display(self, obj):
        description = (
            obj.safe_translation_getter("description", any_language=True)
            or "No description"
        )
        desc_display = (
            description[:80] + "..." if len(description) > 80 else description
        )
        return format_html(
            '<div class="text-sm text-base-700 dark:text-base-300" title="{title}">'
            "{display}"
            "</div>",
            title=description,
            display=desc_display,
        )

    @admin.display(description=_("Changed By"))
    def user_display(self, obj):
        if obj.user:
            return format_html(
                '<div class="text-sm text-base-700 dark:text-base-300">{name}</div>',
                name=obj.user.full_name or obj.user.username,
            )
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300 italic">System</span>'
        )


@admin.register(OrderItemHistory)
class OrderItemHistoryAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
    list_display = [
        "order_item_link",
        "change_type_badge",
        "description_display",
        "user_display",
        "created_at",
    ]
    list_filter = [
        "change_type",
        ("order_item", RelatedDropdownFilter),
        ("user", RelatedDropdownFilter),
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
    list_select_related = ["order_item", "order_item__order", "user"]

    @admin.display(description=_("Order Item"))
    def order_item_link(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<a href="{url}" class="font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300">Item #{item_id}</a>'
            '<div class="text-base-600 dark:text-base-300">Order #{order_id}</div>'
            "</div>",
            url=f"/admin/order/orderitem/{obj.order_item.id}/change/",
            item_id=obj.order_item.id,
            order_id=obj.order_item.order.id,
        )

    @admin.display(description=_("Change Type"))
    def change_type_badge(self, obj):
        type_config = {
            "QUANTITY": {
                "bg": "bg-blue-50 dark:bg-blue-900",
                "text": "text-blue-700 dark:text-blue-300",
                "icon": "🔢",
            },
            "PRICE": {
                "bg": "bg-green-50 dark:bg-green-900",
                "text": "text-green-700 dark:text-green-300",
                "icon": "💲",
            },
            "REFUND": {
                "bg": "bg-red-50 dark:bg-red-900",
                "text": "text-red-700 dark:text-red-300",
                "icon": "↩️",
            },
            "OTHER": {
                "bg": "bg-gray-50 dark:bg-gray-900",
                "text": "text-base-700 dark:text-base-700",
                "icon": "📋",
            },
        }

        config = type_config.get(obj.change_type, type_config["OTHER"])
        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            '{bg} {text_class} rounded-full gap-1">'
            "<span>{icon}</span>"
            "<span>{label}</span>"
            "</span>",
            bg=config["bg"],
            text_class=config["text"],
            icon=config["icon"],
            label=obj.get_change_type_display(),
        )

    @admin.display(description=_("Description"))
    def description_display(self, obj):
        description = (
            obj.safe_translation_getter("description", any_language=True)
            or "No description"
        )
        desc_display = (
            description[:60] + "..." if len(description) > 60 else description
        )
        return format_html(
            '<div class="text-sm text-base-700 dark:text-base-300">{desc}</div>',
            desc=desc_display,
        )

    @admin.display(description=_("Changed By"))
    def user_display(self, obj):
        if obj.user:
            return format_html(
                '<div class="text-sm text-base-700 dark:text-base-300">{name}</div>',
                name=obj.user.full_name or obj.user.username,
            )
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300 italic">System</span>'
        )


@admin.register(StockLog)
class StockLogAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
    list_display = (
        "product",
        "operation_type",
        "quantity_delta",
        "stock_before",
        "stock_after",
        "order",
        "created_at",
        "performed_by",
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
    """Read-mostly archive of rendered invoices.

    Invoices are immutable by convention — Greek tax law forbids edits
    once the number is allocated. This admin exposes browsing, search,
    and per-row download. Use ``OrderAdmin``'s ``Generate invoice``
    detail action to create invoices; ``Regenerate`` there is the only
    way to replace one (consumes a new counter slot).
    """

    list_display = (
        "invoice_number",
        "order_link",
        "issue_date",
        "total_display",
        "currency",
        "document_badge",
        "mydata_status_badge",
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
                    "(seller / buyer / VAT rates) and use the Order "
                    "admin's 'Send to myDATA' action to regenerate."
                ),
            },
        ),
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)

    @admin.display(description=_("Order"))
    def order_link(self, obj):
        if not obj.order_id:
            return "—"
        return format_html(
            '<a href="{url}" class="underline">#{id}</a>',
            url=reverse("admin:order_order_change", args=[obj.order_id]),
            id=obj.order_id,
        )

    @admin.display(description=_("Total"))
    def total_display(self, obj):
        return format_html(
            '<div class="text-sm font-bold">{total}</div>',
            total=str(obj.total),
        )

    @admin.display(description=_("Document"))
    def document_badge(self, obj):
        if not obj.has_document():
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                "⏳ Pending"
                "</span>"
            )
        return format_html(
            '<a href="{url}" target="_blank" rel="noopener" '
            'class="inline-flex items-center px-2 py-1 text-xs font-medium '
            "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 "
            'rounded-full hover:underline">'
            "📄 Download"
            "</a>",
            url=_invoice_download_url(obj) or "",
        )

    @admin.display(description=_("myDATA"))
    def mydata_status_badge(self, obj):
        """One-glance myDATA lifecycle state — colour-coded."""
        # Each state maps to a distinct colour so ops can scan the
        # changelist and spot REJECTED rows instantly. Strings kept
        # inline (not via a helper) so the HTML is auditable in one
        # place.
        from order.models.invoice import MyDataStatus

        state_config = {
            MyDataStatus.NOT_SENT: {
                "bg": "bg-gray-50 dark:bg-gray-900",
                "text": "text-gray-700 dark:text-gray-300",
                "icon": "—",
                "label": "—",
            },
            MyDataStatus.PENDING: {
                "bg": "bg-blue-50 dark:bg-blue-900",
                "text": "text-blue-700 dark:text-blue-300",
                "icon": "⏳",
                "label": "Queued",
            },
            MyDataStatus.SUBMITTED: {
                "bg": "bg-indigo-50 dark:bg-indigo-900",
                "text": "text-indigo-700 dark:text-indigo-300",
                "icon": "↗",
                "label": "Submitted",
            },
            MyDataStatus.CONFIRMED: {
                "bg": "bg-green-50 dark:bg-green-900",
                "text": "text-green-700 dark:text-green-300",
                "icon": "✓",
                "label": "Confirmed",
            },
            MyDataStatus.REJECTED: {
                "bg": "bg-red-50 dark:bg-red-900",
                "text": "text-red-700 dark:text-red-300",
                "icon": "✗",
                "label": "Rejected",
            },
            MyDataStatus.CANCELED: {
                "bg": "bg-yellow-50 dark:bg-yellow-900",
                "text": "text-yellow-700 dark:text-yellow-300",
                "icon": "🚫",
                "label": "Canceled",
            },
        }
        cfg = state_config.get(
            obj.mydata_status, state_config[MyDataStatus.NOT_SENT]
        )
        if obj.mydata_mark:
            mark_suffix = format_html(
                ' <span class="ml-1 text-[10px] opacity-75">#{mark}</span>',
                mark=obj.mydata_mark,
            )
        else:
            mark_suffix = mark_safe("")
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            '{bg} {text_class} rounded-full gap-1">'
            "<span>{icon}</span>"
            "<span>{label}</span>"
            "{mark_suffix}"
            "</span>",
            bg=cfg["bg"],
            text_class=cfg["text"],
            icon=cfg["icon"],
            label=cfg["label"],
            mark_suffix=mark_suffix,
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
