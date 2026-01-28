from datetime import timedelta

from django.contrib import admin
from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
    RangeNumericListFilter,
    RelatedDropdownFilter,
    SliderNumericFilter,
)
from unfold.decorators import action
from unfold.enums import ActionVariant

from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import OrderStatus, PaymentStatus
from order.models.history import OrderHistory, OrderItemHistory
from order.models.item import OrderItem
from order.models.order import Order
from order.models.stock_log import StockLog
from order.services import OrderService


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

    product_display.short_description = _("Product")

    def price_display(self, obj):
        safe_price = conditional_escape(str(obj.price))
        html = f'<div class="text-sm font-medium text-base-900 dark:text-base-100">{safe_price}</div>'
        return mark_safe(html)

    price_display.short_description = _("Unit Price")

    def total_display(self, obj):
        safe_total = conditional_escape(str(obj.total_price))
        html = f'<div class="text-sm font-bold text-base-900 dark:text-base-100">{safe_total}</div>'
        return mark_safe(html)

    total_display.short_description = _("Total")

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

    refund_status.short_description = _("Refund Status")


class OrderHistoryInline(TabularInline):
    model = OrderHistory
    extra = 0
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

    description_display.short_description = _("Description")

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

    user_display.short_description = _("Changed By")


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
                    "shipping_price",
                    "tracking_number",
                    "shipping_carrier",
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
    inlines = [OrderItemInline, OrderHistoryInline]
    save_on_top = True
    date_hierarchy = "created_at"
    list_select_related = ["user", "country", "region", "pay_way"]

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

    customer_info.short_description = _("Customer")

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

    order_summary.short_description = _("Order Summary")

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

    payment_info.short_description = _("Payment")

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

    shipping_info.short_description = _("Shipping")

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

    created_display.short_description = _("Created")

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

    urgency_indicator.short_description = _("Priority")

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

    status_badge.short_description = _("Status")

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

    document_type_badge.short_description = _("Document Type")

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

    currency_status.short_description = _("Currency")

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

    customer_summary.short_description = _("Customer Summary")

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

    financial_summary.short_description = _("Financial Summary")

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

    shipping_summary.short_description = _("Shipping Summary")

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

    order_analytics.short_description = _("Order Analytics")

    @action(
        description=_("Mark selected orders as processing"),
        variant=ActionVariant.PRIMARY,
        icon="play_arrow",
    )
    def mark_as_processing(self, request, queryset):
        for order in queryset:
            try:
                OrderService.update_order_status(order, OrderStatus.PROCESSING)
                self.message_user(
                    request,
                    _("Order %(order_id)s marked as processing")
                    % {"order_id": order.id},
                )
            except ValueError as e:
                self.message_user(request, f"Error: {e!s}", level="error")

    @action(
        description=_("Mark selected orders as shipped"),
        variant=ActionVariant.INFO,
        icon="local_shipping",
    )
    def mark_as_shipped(self, request, queryset):
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
        description=_("Mark selected orders as delivered"),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def mark_as_delivered(self, request, queryset):
        for order in queryset:
            try:
                OrderService.update_order_status(order, OrderStatus.DELIVERED)
                self.message_user(
                    request,
                    _("Order %(order_id)s marked as delivered")
                    % {"order_id": order.id},
                )
            except ValueError as e:
                self.message_user(request, f"Error: {e!s}", level="error")

    @action(
        description=_("Mark selected orders as completed"),
        variant=ActionVariant.SUCCESS,
        icon="task_alt",
    )
    def mark_as_completed(self, request, queryset):
        for order in queryset:
            try:
                OrderService.update_order_status(order, OrderStatus.COMPLETED)
                self.message_user(
                    request,
                    _("Order %(order_id)s marked as completed")
                    % {"order_id": order.id},
                )
            except ValueError as e:
                self.message_user(request, f"Error: {e!s}", level="error")

    @action(
        description=_("Cancel selected orders and restore stock"),
        variant=ActionVariant.DANGER,
        icon="cancel",
    )
    def mark_as_canceled(self, request, queryset):
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

    order_link.short_description = _("Order")

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

    product_display.short_description = _("Product")

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

    quantity_display.short_description = _("Quantity")

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

    pricing_info.short_description = _("Pricing")

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

    refund_status_display.short_description = _("Refund Status")

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

    item_analytics.short_description = _("Item Analytics")


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

    order_link.short_description = _("Order")

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

    change_type_badge.short_description = _("Change Type")

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

    description_display.short_description = _("Description")

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

    user_display.short_description = _("Changed By")


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

    order_item_link.short_description = _("Order Item")

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

    change_type_badge.short_description = _("Change Type")

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

    description_display.short_description = _("Description")

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

    user_display.short_description = _("Changed By")


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
