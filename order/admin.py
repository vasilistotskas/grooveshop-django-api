import logging
from datetime import timedelta

from django.contrib import admin, messages
from django.db.models import Count, Sum
from django.http import FileResponse, Http404
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import TabularInline

from admin.base import BaseModelAdmin
from unfold.contrib.filters.admin import (
    AutocompleteSelectFilter,
    DropdownFilter,
    RangeDateFilter,
    RangeDateTimeFilter,
    RangeNumericListFilter,
    RelatedDropdownFilter,
    SliderNumericFilter,
)
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from admin.displays import (
    ORDER_STATUS_VARIANT,
    PAYMENT_STATUS_VARIANT,
    SHIPMENT_STATE_VARIANT,
    choice_label,
    format_dt,
    header_two_line,
    money,
    relative_time,
)
from admin.mixins import IsSuperuserOnlyModelAdmin
from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import OrderStatus, PaymentStatus
from order.invoicing import generate_invoice
from order.models.history import OrderHistory, OrderItemHistory
from order.models.invoice import Invoice, InvoiceCounter, MyDataStatus
from order.models.item import OrderItem
from order.models.order import Order
from order.models.stock_log import StockLog
from order.models.viva_webhook_event import VivaWebhookEvent
from order.services import OrderService

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


@admin.register(Order)
class OrderAdmin(BaseModelAdmin):
    list_display = [
        "status_label",
        "customer",
        "order_summary",
        "payment_status_label",
        "shipment_state",
        "shipping_info",
        "created",
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
        "created_at",
        "updated_at",
        "status_updated_at",
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
                    "paid_amount",
                ),
                "classes": ("tab",),
                "description": _(
                    "Note: Ensure all money fields use the same currency "
                    "(EUR preferred) to avoid calculation errors."
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
                "fields": ("customer_notes",),
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
        # Registry-driven inline routing keyed on
        # ``obj.shipping_provider.code``. Rows with no provider set
        # (pre-registry data) get neither carrier inline; their data
        # still lives on the order itself and can be reviewed from
        # the change form fields.
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
            .annotate(
                item_count=Count("items"),
                total_items_quantity=Sum("items__quantity"),
            )
            .select_related(
                "user",
                "country",
                "region",
                "pay_way",
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

    @display(description=_("Customer"), header=True, ordering="last_name")
    def customer(self, obj):
        return header_two_line(obj.customer_full_name, obj.email)

    @display(description=_("Order"), ordering="created_at")
    def order_summary(self, obj):
        item_count = getattr(obj, "item_count", 0)
        total_qty = getattr(obj, "total_items_quantity", 0) or 0

        try:
            total = money(obj.total_price.amount)
        except ValueError:
            total = _(
                "items %(items)s + shipping %(shipping)s (currency mismatch)"
            ) % {
                "items": money(obj.total_price_items.amount),
                "shipping": money(obj.total_price_extra.amount),
            }

        return f"{item_count} items, qty {total_qty} — {total}"

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
        description=str(_("Mark selected orders as processing")),
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
        description=str(_("Mark selected orders as shipped")),
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
        description=str(_("Mark selected orders as delivered")),
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
        description=str(_("Mark selected orders as completed")),
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
        description=str(_("Cancel selected orders and restore stock")),
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
                OrderService.cancel_order(order)
                self.message_user(
                    request,
                    _("Order %(order_id)s marked as canceled")
                    % {"order_id": order.id},
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
        except Exception:  # noqa: BLE001
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
        return format_html(
            '<a href="{url}">Order #{id}</a>',
            url=f"/admin/order/order/{obj.order.id}/change/",
            id=obj.order.id,
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

    change_type_label = choice_label(
        "change_type",
        variants=ORDER_HISTORY_CHANGE_TYPE_VARIANT,
        description=_("Change Type"),
    )

    @admin.display(description=_("Order"))
    def order_link(self, obj):
        return format_html(
            '<a href="{url}">Order #{id}</a>',
            url=f"/admin/order/order/{obj.order.id}/change/",
            id=obj.order.id,
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
    list_select_related = ["order_item", "order_item__order", "user"]

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
            '<a href="{url}">Item #{item_id}</a> (Order #{order_id})',
            url=f"/admin/order/orderitem/{obj.order_item.id}/change/",
            item_id=obj.order_item.id,
            order_id=obj.order_item.order.id,
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
            '<a href="{url}">#{id}</a>',
            url=reverse("admin:order_order_change", args=[obj.order_id]),
            id=obj.order_id,
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
