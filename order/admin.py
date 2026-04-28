import logging
from datetime import timedelta

from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Count, Sum
from django.http import FileResponse, Http404
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, TabularInline
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

from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import OrderStatus, PaymentStatus
from order.invoicing import generate_invoice
from order.models.history import OrderHistory, OrderItemHistory
from order.models.invoice import Invoice, InvoiceCounter
from order.models.item import OrderItem
from order.models.order import Order
from order.models.stock_log import StockLog
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
            product_name = (
                obj.product.safe_translation_getter("name", any_language=True)
                or "Unnamed Product"
            )
            safe_name = conditional_escape(product_name)
            safe_id = conditional_escape(str(obj.product.id))

            html = (
                '<div class="text-sm">'
                f'<div class="font-medium text-base-900 dark:text-base-100">{safe_name}</div>'
                f'<div class="text-base-600 dark:text-base-300">ID: {safe_id}</div>'
                "</div>"
            )
            return mark_safe(html)
        return "-"

    @admin.display(description=_("Unit Price"))
    def price_display(self, obj):
        safe_price = conditional_escape(str(obj.price))
        html = f'<div class="text-sm font-medium text-base-900 dark:text-base-100">{safe_price}</div>'
        return mark_safe(html)

    @admin.display(description=_("Total"))
    def total_display(self, obj):
        safe_total = conditional_escape(str(obj.total_price))
        html = f'<div class="text-sm font-bold text-base-900 dark:text-base-100">{safe_total}</div>'
        return mark_safe(html)

    @admin.display(description=_("Refund Status"))
    def refund_status(self, obj):
        if obj.is_refunded:
            html = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "↩️ Refunded"
                "</span>"
            )
            return mark_safe(html)
        elif obj.refunded_quantity > 0:
            html = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                "⚠️ Partial"
                "</span>"
            )
            return mark_safe(html)
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
        safe_desc = conditional_escape(desc_display)

        html = f'<div class="text-sm text-base-700 dark:text-base-300">{safe_desc}</div>'
        return mark_safe(html)

    @admin.display(description=_("Changed By"))
    def user_display(self, obj):
        if obj.user:
            safe_name = conditional_escape(
                obj.user.full_name or obj.user.username
            )
            html = f'<div class="text-sm text-base-700 dark:text-base-300">{safe_name}</div>'
            return mark_safe(html)
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
        url = _invoice_download_url(obj)
        safe_url = conditional_escape(url or "")
        html = (
            '<a href="' + safe_url + '" target="_blank" rel="noopener" '
            'class="inline-flex items-center px-2 py-1 text-xs font-medium '
            "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 "
            'rounded-full hover:underline">'
            "📄 Download PDF"
            "</a>"
        )
        return mark_safe(html)


@admin.register(Order)
class OrderAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

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
        # Filter Orders by ``home_delivery`` vs ``box_now_locker`` —
        # useful for support tickets & shipping batch operations.
        "shipping_method",
        # Filter on the BoxNow parcel state via the OneToOne reverse
        # relation. Only fires for box_now_locker orders.
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
    readonly_fields = (
        "uuid",
        "created_at",
        "updated_at",
        "status_updated_at",
        "order_analytics",
        "financial_summary",
        "customer_summary",
        "shipping_summary",
        # ``shipping_method`` is set at order-creation time and is
        # read-only after; ``boxnow_summary`` is a computed display that
        # surfaces BoxNow parcel state inline in the Shipping fieldset
        # so admins don't have to scroll to the inline below.
        "shipping_method",
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
                "classes": ("wide",),
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
                "classes": ("wide",),
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
                "classes": ("wide",),
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
                "classes": ("wide",),
                "description": _(
                    "⚠️ Note: Ensure all money fields use the same currency (EUR preferred) to avoid calculation errors."
                ),
            },
        ),
        (
            _("Shipping & Tracking"),
            {
                "fields": (
                    "shipping_method",
                    "shipping_price",
                    "tracking_number",
                    "shipping_carrier",
                    "boxnow_summary",
                ),
                "classes": ("wide",),
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
                "classes": ("collapse",),
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
    actions_detail = [
        "generate_invoice_now",
        "regenerate_invoice",
        "send_invoice_to_mydata_now",
        "cancel_mydata_invoice_now",
    ]
    inlines = [OrderItemInline, InvoiceInline, OrderHistoryInline]
    save_on_top = True
    date_hierarchy = "created_at"
    list_select_related = ["user", "country", "region", "pay_way"]

    def get_inlines(self, request, obj=None):
        # Show the BoxNow shipment inline only when the order actually
        # uses BoxNow — keeps the change form clean for home_delivery
        # orders. Lazy import sidesteps the circular ``order ↔ shipping_boxnow``
        # registration cycle at app-load time.
        inlines = list(super().get_inlines(request, obj))
        if obj and obj.shipping_method == "box_now_locker":
            from shipping_boxnow.admin import (  # noqa: PLC0415
                BoxNowShipmentOrderInline,
            )

            inlines.append(BoxNowShipmentOrderInline)
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
        full_name = f"{obj.first_name} {obj.last_name}"
        safe_name = conditional_escape(full_name)
        safe_email = conditional_escape(obj.email)
        safe_phone = conditional_escape(obj.phone or "No phone")

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_name}</div>'
            f'<div class="text-base-600 dark:text-base-300">{safe_email}</div>'
            f'<div class="text-base-600 dark:text-base-300">{safe_phone}</div>'
            "</div>"
        )
        return mark_safe(html)

    @admin.display(description=_("Order Summary"))
    def order_summary(self, obj):
        item_count = getattr(obj, "item_count", 0)
        total_qty = getattr(obj, "total_items_quantity", 0)

        try:
            total_price = obj.total_price
            price_display = conditional_escape(str(total_price))
        except ValueError as e:
            items_total = obj.total_price_items
            shipping_total = obj.total_price_extra
            safe_error = conditional_escape(str(e))
            safe_items = conditional_escape(str(items_total))
            safe_shipping = conditional_escape(str(shipping_total))

            price_display = (
                f'<span class="text-red-600 dark:text-red-400" title="Currency mismatch: {safe_error}">'
                f"Items: {safe_items} + Ship: {safe_shipping}"
                "</span>"
            )

        safe_count = conditional_escape(str(item_count))
        safe_qty = conditional_escape(str(total_qty or 0))

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_count} items</div>'
            f'<div class="text-base-600 dark:text-base-300">Qty: {safe_qty}</div>'
            f'<div class="font-bold text-base-900 dark:text-base-100">{price_display}</div>'
            "</div>"
        )
        return mark_safe(html)

    @admin.display(description=_("Payment"))
    def payment_info(self, obj):
        payment_badge = self.payment_status_badge(obj)
        paid_amount = obj.paid_amount or obj.total_price
        safe_amount = conditional_escape(str(paid_amount))
        safe_method = conditional_escape(obj.payment_method or "Not set")

        html = (
            '<div class="text-sm">'
            f"<div>{payment_badge}</div>"
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_amount}</div>'
            f'<div class="text-base-600 dark:text-base-300">{safe_method}</div>'
            "</div>"
        )
        return mark_safe(html)

    @admin.display(description=_("BoxNow Summary"))
    def boxnow_summary(self, obj):
        """Compact summary of the BoxNow shipment shown inline in
        the Shipping & Tracking fieldset. Renders nothing for non-BoxNow
        orders. For BoxNow orders, surfaces parcel state, voucher, and
        chosen locker so admins don't have to scroll to the inline.
        """
        if obj.shipping_method != "box_now_locker":
            return mark_safe(
                '<span class="text-sm text-base-500">'
                + str(_("Not a BoxNow order"))
                + "</span>"
            )

        shipment = getattr(obj, "boxnow_shipment", None)
        if shipment is None:
            return mark_safe(
                '<span class="text-sm text-orange-600">'
                + str(
                    _(
                        "shipping_method=box_now_locker but no "
                        "BoxNowShipment row — order created before the "
                        "BoxNow integration shipped, or a service-layer "
                        "bug. Inspect order.history for clues."
                    )
                )
                + "</span>"
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

        state_label = conditional_escape(shipment.get_parcel_state_display())
        voucher = (
            f'<div class="font-mono text-sm">{conditional_escape(shipment.parcel_id)}</div>'
            if shipment.parcel_id
            else (
                '<div class="text-sm text-orange-600">'
                + str(_("Voucher pending — fires after payment"))
                + "</div>"
            )
        )
        locker_id = conditional_escape(shipment.locker_external_id or "—")
        locker_name = ""
        if shipment.locker is not None:
            locker_name = (
                f" &middot; {conditional_escape(shipment.locker.name)}"
            )

        return mark_safe(
            '<div class="space-y-1 text-sm">'
            f'<div><span class="rounded px-2 py-0.5 text-xs font-medium {state_color}">{state_label}</span></div>'
            f"<div><strong>{_('Voucher')}:</strong> {voucher}</div>"
            f"<div><strong>{_('Locker')}:</strong> "
            f'<span class="font-mono">{locker_id}</span>{locker_name}</div>'
            "</div>"
        )

    @admin.display(description=_("Shipping"))
    def shipping_info(self, obj):
        safe_city = conditional_escape(obj.city)

        if obj.tracking_number:
            safe_tracking = conditional_escape(obj.tracking_number)
            safe_carrier = conditional_escape(
                obj.shipping_carrier or "Unknown carrier"
            )

            html = (
                '<div class="text-sm">'
                f'<div class="font-medium text-blue-600 dark:text-blue-400">{safe_tracking}</div>'
                f'<div class="text-base-600 dark:text-base-300">{safe_carrier}</div>'
                f'<div class="text-base-600 dark:text-base-300">{safe_city}</div>'
                "</div>"
            )
        else:
            html = (
                '<div class="text-sm">'
                '<div class="text-base-600 dark:text-base-300">No tracking</div>'
                f'<div class="text-base-600 dark:text-base-300">{safe_city}</div>'
                "</div>"
            )
        return mark_safe(html)

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

        safe_date = conditional_escape(
            obj.created_at.strftime("%Y-%m-%d %H:%M")
        )
        safe_time = conditional_escape(time_ago)

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_date}</div>'
            f'<div class="{color}">{safe_time}</div>'
            "</div>"
        )
        return mark_safe(html)

    @admin.display(description=_("Priority"))
    def urgency_indicator(self, obj):
        if not obj.created_at:
            return "Available after creation."

        now = timezone.now()
        age = now - obj.created_at

        if obj.status == OrderStatus.PENDING and age > timedelta(hours=24):
            html = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "🚨 Urgent"
                "</span>"
            )
            return mark_safe(html)
        elif obj.status == OrderStatus.PROCESSING and age > timedelta(days=3):
            html = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                "⚠️ Delayed"
                "</span>"
            )
            return mark_safe(html)
        elif obj.status == OrderStatus.SHIPPED and age > timedelta(days=7):
            html = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded-full">'
                "📦 Follow up"
                "</span>"
            )
            return mark_safe(html)
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

        safe_status = conditional_escape(obj.get_status_display())

        html = (
            f'<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            f'{config["bg"]} {config["text"]} rounded-full gap-1">'
            f"<span>{config['icon']}</span>"
            f"<span>{safe_status}</span>"
            "</span>"
        )
        return mark_safe(html)

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

        safe_payment = conditional_escape(obj.get_payment_status_display())

        html = (
            f'<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            f'{config["bg"]} {config["text"]} rounded-full gap-1">'
            f"<span>{config['icon']}</span>"
            f"<span>{safe_payment}</span>"
            "</span>"
        )
        return mark_safe(html)

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

        safe_doc = conditional_escape(obj.get_document_type_display())

        html = (
            f'<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            f'{config["bg"]} {config["text"]} rounded-full gap-1">'
            f"<span>{config['icon']}</span>"
            f"<span>{safe_doc}</span>"
            "</span>"
        )
        return mark_safe(html)

    @admin.display(description=_("Currency"))
    def currency_status(self, obj):
        try:
            items_currency = obj.total_price_items.currency
            shipping_currency = obj.shipping_price.currency

            if items_currency == shipping_currency:
                safe_currency = conditional_escape(str(items_currency))
                html = (
                    '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                    'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                    f"✅ {safe_currency}"
                    "</span>"
                )
            else:
                html = (
                    '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                    'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                    "⚠️ Mixed"
                    "</span>"
                )
        except ValueError:
            html = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "❌ Mismatch"
                "</span>"
            )
        return mark_safe(html)

    @admin.display(description=_("Customer Summary"))
    def customer_summary(self, obj):
        safe_name = conditional_escape(obj.customer_full_name)
        safe_email = conditional_escape(obj.email)
        safe_phone = conditional_escape(obj.phone or "Not provided")
        account_status = "Registered User" if obj.user else "Guest"

        html = (
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Full Name:</strong></div><div>{safe_name}</div>"
            f"<div><strong>Email:</strong></div><div>{safe_email}</div>"
            f"<div><strong>Phone:</strong></div><div>{safe_phone}</div>"
            f"<div><strong>Account:</strong></div><div>{account_status}</div>"
            "</div>"
            "</div>"
        )
        return mark_safe(html)

    @admin.display(description=_("Financial Summary"))
    def financial_summary(self, obj):
        shipping = obj.shipping_price
        items_total = obj.total_price_items

        safe_shipping = conditional_escape(str(shipping))
        safe_items = conditional_escape(str(items_total))

        try:
            total = obj.total_price
            total_display = conditional_escape(str(total))
            currency_warning = ""
        except ValueError as e:
            total_display = '<span class="text-red-600 dark:text-red-400">Currency Mismatch</span>'
            safe_error = conditional_escape(str(e))
            currency_warning = (
                "<div><strong>Currency Issue:</strong></div>"
                f'<div class="text-red-600 dark:text-red-400 text-xs">{safe_error}</div>'
            )

        safe_paid = conditional_escape(str(obj.paid_amount or "Not paid"))
        safe_payment_status = conditional_escape(
            obj.get_payment_status_display()
        )
        safe_payment_method = conditional_escape(
            obj.payment_method or "Not specified"
        )
        safe_doc_type = conditional_escape(obj.get_document_type_display())

        html = (
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Items Total:</strong></div><div>{safe_items}</div>"
            f"<div><strong>Shipping:</strong></div><div>{safe_shipping}</div>"
            f'<div><strong>Grand Total:</strong></div><div class="font-bold">{total_display}</div>'
            f"<div><strong>Paid Amount:</strong></div><div>{safe_paid}</div>"
            f"<div><strong>Payment Status:</strong></div><div>{safe_payment_status}</div>"
            f"<div><strong>Payment Method:</strong></div><div>{safe_payment_method}</div>"
            f"<div><strong>Document Type:</strong></div><div>{safe_doc_type}</div>"
            f"{currency_warning}"
            "</div>"
            "</div>"
        )
        return mark_safe(html)

    @admin.display(description=_("Shipping Summary"))
    def shipping_summary(self, obj):
        safe_address = conditional_escape(obj.full_address)
        safe_tracking = conditional_escape(
            obj.tracking_number or "Not assigned"
        )
        safe_carrier = conditional_escape(
            obj.shipping_carrier or "Not assigned"
        )
        safe_price = conditional_escape(str(obj.shipping_price))

        html = (
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Full Address:</strong></div><div>{safe_address}</div>"
            f"<div><strong>Tracking:</strong></div><div>{safe_tracking}</div>"
            f"<div><strong>Carrier:</strong></div><div>{safe_carrier}</div>"
            f"<div><strong>Shipping Cost:</strong></div><div>{safe_price}</div>"
            "</div>"
            "</div>"
        )
        return mark_safe(html)

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

        safe_days = conditional_escape(str(age.days))
        safe_hours = conditional_escape(str(age.seconds // 3600))
        safe_processing = conditional_escape(processing_time or "N/A")
        safe_count = conditional_escape(str(getattr(obj, "item_count", 0)))
        can_cancel = "Yes" if obj.can_be_canceled else "No"
        is_paid = "Yes" if obj.is_paid else "No"
        safe_currency = conditional_escape(currency_status)
        safe_docs = conditional_escape(doc_types)

        currency_class = (
            "text-red-600 dark:text-red-400"
            if "Mismatch" in currency_status or "Mixed" in currency_status
            else ""
        )

        html = (
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Order Age:</strong></div><div>{safe_days}d {safe_hours}h</div>"
            f"<div><strong>Status Age:</strong></div><div>{safe_processing}</div>"
            f"<div><strong>Items Count:</strong></div><div>{safe_count}</div>"
            f"<div><strong>Can Cancel:</strong></div><div>{can_cancel}</div>"
            f"<div><strong>Is Paid:</strong></div><div>{is_paid}</div>"
            f'<div><strong>Currency Status:</strong></div><div class="{currency_class}">{safe_currency}</div>'
            f'<div><strong>Available Docs:</strong></div><div class="text-xs">{safe_docs}</div>'
            "</div>"
            "</div>"
        )
        return mark_safe(html)

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


@admin.register(OrderItem)
class OrderItemAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

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
        safe_url = conditional_escape(
            f"/admin/order/order/{obj.order.id}/change/"
        )
        safe_id = conditional_escape(str(obj.order.id))
        status_badge = self.order_status_mini(obj.order)

        html = (
            '<div class="text-sm">'
            f'<a href="{safe_url}" class="font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300">Order #{safe_id}</a>'
            f"<div>{status_badge}</div>"
            "</div>"
        )
        return mark_safe(html)

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
        safe_status = conditional_escape(order.get_status_display())

        html = f'<span class="{color}">{safe_status}</span>'
        return mark_safe(html)

    @admin.display(description=_("Product"))
    def product_display(self, obj):
        product_name = (
            obj.product.safe_translation_getter("name", any_language=True)
            or "Unnamed Product"
        )
        safe_name = conditional_escape(product_name)
        safe_id = conditional_escape(str(obj.product.id))

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_name}</div>'
            f'<div class="text-base-600 dark:text-base-300">ID: {safe_id}</div>'
            "</div>"
        )
        return mark_safe(html)

    @admin.display(description=_("Quantity"))
    def quantity_display(self, obj):
        if obj.refunded_quantity > 0:
            safe_qty = conditional_escape(str(obj.quantity))
            safe_refunded = conditional_escape(str(obj.refunded_quantity))
            safe_net = conditional_escape(str(obj.net_quantity))

            html = (
                '<div class="text-sm">'
                f'<div class="font-medium text-base-900 dark:text-base-100">Total: {safe_qty}</div>'
                f'<div class="text-red-600 dark:text-red-400">Refunded: {safe_refunded}</div>'
                f'<div class="text-green-600 dark:text-green-400">Net: {safe_net}</div>'
                "</div>"
            )
        else:
            safe_qty = conditional_escape(str(obj.quantity))
            html = (
                f'<span class="inline-flex items-center px-3 py-1 text-sm font-medium '
                f'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">'
                f"x{safe_qty}"
                "</span>"
            )
        return mark_safe(html)

    @admin.display(description=_("Pricing"))
    def pricing_info(self, obj):
        safe_price = conditional_escape(str(obj.price))
        safe_total = conditional_escape(str(obj.total_price))
        safe_net = conditional_escape(str(obj.net_price))

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_price} each</div>'
            f'<div class="font-bold text-base-900 dark:text-base-100">Total: {safe_total}</div>'
            f'<div class="text-green-600 dark:text-green-400">Net: {safe_net}</div>'
            "</div>"
        )
        return mark_safe(html)

    @admin.display(description=_("Refund Status"))
    def refund_status_display(self, obj):
        if obj.is_refunded:
            html = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "↩️ Fully Refunded"
                "</span>"
            )
        elif obj.refunded_quantity > 0:
            safe_refunded = conditional_escape(str(obj.refunded_quantity))
            safe_qty = conditional_escape(str(obj.quantity))
            html = (
                f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                f'bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                f"⚠️ Partial ({safe_refunded}/{safe_qty})"
                "</span>"
            )
        else:
            html = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                "✅ Active"
                "</span>"
            )
        return mark_safe(html)

    @admin.display(description=_("Item Analytics"))
    def item_analytics(self, obj):
        safe_original = conditional_escape(
            str(obj.original_quantity or obj.quantity)
        )
        safe_current = conditional_escape(str(obj.quantity))
        safe_refunded = conditional_escape(str(obj.refunded_quantity))
        safe_net_qty = conditional_escape(str(obj.net_quantity))
        safe_refunded_amt = conditional_escape(str(obj.refunded_amount))
        safe_net_price = conditional_escape(str(obj.net_price))

        html = (
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Original Qty:</strong></div><div>{safe_original}</div>"
            f"<div><strong>Current Qty:</strong></div><div>{safe_current}</div>"
            f"<div><strong>Refunded Qty:</strong></div><div>{safe_refunded}</div>"
            f"<div><strong>Net Qty:</strong></div><div>{safe_net_qty}</div>"
            f"<div><strong>Refunded Amount:</strong></div><div>{safe_refunded_amt}</div>"
            f"<div><strong>Net Amount:</strong></div><div>{safe_net_price}</div>"
            "</div>"
            "</div>"
        )
        return mark_safe(html)


@admin.register(OrderHistory)
class OrderHistoryAdmin(ModelAdmin):
    compressed_fields = True
    list_fullwidth = True
    list_filter_sheet = True

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
        safe_url = conditional_escape(
            f"/admin/order/order/{obj.order.id}/change/"
        )
        safe_id = conditional_escape(str(obj.order.id))

        html = (
            f'<a href="{safe_url}" class="font-medium text-blue-600 dark:text-blue-400 '
            f'hover:text-blue-800 dark:hover:text-blue-300">Order #{safe_id}</a>'
        )
        return mark_safe(html)

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

        safe_type = conditional_escape(obj.get_change_type_display())

        html = (
            f'<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            f'{config["bg"]} {config["text"]} rounded-full gap-1">'
            f"<span>{config['icon']}</span>"
            f"<span>{safe_type}</span>"
            "</span>"
        )
        return mark_safe(html)

    @admin.display(description=_("Description"))
    def description_display(self, obj):
        description = (
            obj.safe_translation_getter("description", any_language=True)
            or "No description"
        )
        desc_display = (
            description[:80] + "..." if len(description) > 80 else description
        )
        safe_desc = conditional_escape(description)
        safe_display = conditional_escape(desc_display)

        html = (
            f'<div class="text-sm text-base-700 dark:text-base-300" title="{safe_desc}">'
            f"{safe_display}"
            "</div>"
        )
        return mark_safe(html)

    @admin.display(description=_("Changed By"))
    def user_display(self, obj):
        if obj.user:
            safe_name = conditional_escape(
                obj.user.full_name or obj.user.username
            )
            html = f'<div class="text-sm text-base-700 dark:text-base-300">{safe_name}</div>'
            return mark_safe(html)
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300 italic">System</span>'
        )


@admin.register(OrderItemHistory)
class OrderItemHistoryAdmin(ModelAdmin):
    compressed_fields = True
    list_fullwidth = True
    list_filter_sheet = True

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
        safe_url = conditional_escape(
            f"/admin/order/orderitem/{obj.order_item.id}/change/"
        )
        safe_item_id = conditional_escape(str(obj.order_item.id))
        safe_order_id = conditional_escape(str(obj.order_item.order.id))

        html = (
            '<div class="text-sm">'
            f'<a href="{safe_url}" class="font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300">Item #{safe_item_id}</a>'
            f'<div class="text-base-600 dark:text-base-300">Order #{safe_order_id}</div>'
            "</div>"
        )
        return mark_safe(html)

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
        safe_type = conditional_escape(obj.get_change_type_display())

        html = (
            f'<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            f'{config["bg"]} {config["text"]} rounded-full gap-1">'
            f"<span>{config['icon']}</span>"
            f"<span>{safe_type}</span>"
            "</span>"
        )
        return mark_safe(html)

    @admin.display(description=_("Description"))
    def description_display(self, obj):
        description = (
            obj.safe_translation_getter("description", any_language=True)
            or "No description"
        )
        desc_display = (
            description[:60] + "..." if len(description) > 60 else description
        )
        safe_desc = conditional_escape(desc_display)

        html = f'<div class="text-sm text-base-700 dark:text-base-300">{safe_desc}</div>'
        return mark_safe(html)

    @admin.display(description=_("Changed By"))
    def user_display(self, obj):
        if obj.user:
            safe_name = conditional_escape(
                obj.user.full_name or obj.user.username
            )
            html = f'<div class="text-sm text-base-700 dark:text-base-300">{safe_name}</div>'
            return mark_safe(html)
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300 italic">System</span>'
        )


@admin.register(StockLog)
class StockLogAdmin(ModelAdmin):
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
class InvoiceAdmin(ModelAdmin):
    """Read-mostly archive of rendered invoices.

    Invoices are immutable by convention — Greek tax law forbids edits
    once the number is allocated. This admin exposes browsing, search,
    and per-row download. Use ``OrderAdmin``'s ``Generate invoice``
    detail action to create invoices; ``Regenerate`` there is the only
    way to replace one (consumes a new counter slot).
    """

    compressed_fields = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

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
        url = reverse("admin:order_order_change", args=[obj.order_id])
        safe_url = conditional_escape(url)
        safe_id = conditional_escape(str(obj.order_id))
        html = f'<a href="{safe_url}" class="underline">#{safe_id}</a>'
        return mark_safe(html)

    @admin.display(description=_("Total"))
    def total_display(self, obj):
        safe_total = conditional_escape(str(obj.total))
        html = f'<div class="text-sm font-bold">{safe_total}</div>'
        return mark_safe(html)

    @admin.display(description=_("Document"))
    def document_badge(self, obj):
        if not obj.has_document():
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                "⏳ Pending"
                "</span>"
            )
        url = _invoice_download_url(obj)
        safe_url = conditional_escape(url or "")
        html = (
            '<a href="' + safe_url + '" target="_blank" rel="noopener" '
            'class="inline-flex items-center px-2 py-1 text-xs font-medium '
            "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 "
            'rounded-full hover:underline">'
            "📄 Download"
            "</a>"
        )
        return mark_safe(html)

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
        mark_suffix = ""
        if obj.mydata_mark:
            mark_suffix = f' <span class="ml-1 text-[10px] opacity-75">#{obj.mydata_mark}</span>'
        html = (
            f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            f'{cfg["bg"]} {cfg["text"]} rounded-full gap-1">'
            f"<span>{cfg['icon']}</span>"
            f"<span>{conditional_escape(cfg['label'])}</span>"
            f"{mark_suffix}"
            "</span>"
        )
        return mark_safe(html)


@admin.register(InvoiceCounter)
class InvoiceCounterAdmin(ModelAdmin):
    """Per-year sequential invoice counter.

    Editable by superusers only — bumping ``next_number`` is an ops
    action (e.g. reserving ``INV-2026-000001..100`` for legacy imports).
    Regular staff should never touch it; a wrong value corrupts the
    sequence across all future invoices that year.
    """

    compressed_fields = True
    list_fullwidth = True

    list_display = ("year", "next_number")
    ordering = ("-year",)
    readonly_fields = ()

    def has_add_permission(self, request):
        return bool(request.user and request.user.is_superuser)

    def has_change_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)
