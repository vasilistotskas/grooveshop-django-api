from datetime import timedelta

from django.contrib import admin
from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.html import format_html
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
            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
                '<div class="text-base-500 dark:text-base-400">ID: {}</div>'
                "</div>",
                product_name,
                obj.product.id,
            )
        return "-"

    product_display.short_description = _("Product")

    def price_display(self, obj):
        return format_html(
            '<div class="text-sm font-medium text-base-900 dark:text-base-100">{}</div>',
            obj.price,
        )

    price_display.short_description = _("Unit Price")

    def total_display(self, obj):
        return format_html(
            '<div class="text-sm font-bold text-base-900 dark:text-base-100">{}</div>',
            obj.total_price,
        )

    total_display.short_description = _("Total")

    def refund_status(self, obj):
        if obj.is_refunded:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "↩️ Refunded"
                "</span>"
            )
        elif obj.refunded_quantity > 0:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                "⚠️ Partial"
                "</span>"
            )
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
        return format_html(
            '<div class="text-sm text-base-700 dark:text-base-300">{}</div>',
            description[:100] + "..."
            if len(description) > 100
            else description,
        )

    description_display.short_description = _("Description")

    def user_display(self, obj):
        if obj.user:
            return format_html(
                '<div class="text-sm text-base-700 dark:text-base-300">{}</div>',
                obj.user.full_name or obj.user.username,
            )
        return format_html(
            '<span class="text-base-400 dark:text-base-500">System</span>'
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
        "mobile_phone",
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
                    "mobile_phone",
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
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="text-base-500 dark:text-base-400">{}</div>'
            '<div class="text-base-500 dark:text-base-400">{}</div>'
            "</div>",
            full_name,
            obj.email,
            obj.phone or "No phone",
        )

    customer_info.short_description = _("Customer")

    def order_summary(self, obj):
        item_count = getattr(obj, "item_count", 0)
        total_qty = getattr(obj, "total_items_quantity", 0)

        try:
            total_price = obj.total_price
            price_display = str(total_price)
        except ValueError as e:
            items_total = obj.total_price_items
            shipping_total = obj.total_price_extra
            price_display = format_html(
                '<span class="text-red-600 dark:text-red-400" title="Currency mismatch: {}">'
                "Items: {} + Ship: {}"
                "</span>",
                str(e),
                items_total,
                shipping_total,
            )

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{} items</div>'
            '<div class="text-base-500 dark:text-base-400">Qty: {}</div>'
            '<div class="font-bold text-base-900 dark:text-base-100">{}</div>'
            "</div>",
            item_count,
            total_qty or 0,
            price_display,
        )

    order_summary.short_description = _("Order Summary")

    def payment_info(self, obj):
        payment_badge = self.payment_status_badge(obj)
        paid_amount = obj.paid_amount or obj.total_price

        return format_html(
            '<div class="text-sm">'
            "<div>{}</div>"
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="text-base-500 dark:text-base-400">{}</div>'
            "</div>",
            payment_badge,
            paid_amount,
            obj.payment_method or "Not set",
        )

    payment_info.short_description = _("Payment")

    def shipping_info(self, obj):
        if obj.tracking_number:
            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-blue-600 dark:text-blue-400">{}</div>'
                '<div class="text-base-500 dark:text-base-400">{}</div>'
                '<div class="text-base-500 dark:text-base-400">{}</div>'
                "</div>",
                obj.tracking_number,
                obj.shipping_carrier or "Unknown carrier",
                obj.city,
            )
        else:
            return format_html(
                '<div class="text-sm">'
                '<div class="text-base-500 dark:text-base-400">No tracking</div>'
                '<div class="text-base-500 dark:text-base-400">{}</div>'
                "</div>",
                obj.city,
            )

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

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="{}">{}</div>'
            "</div>",
            obj.created_at.strftime("%Y-%m-%d %H:%M"),
            color,
            time_ago,
        )

    created_display.short_description = _("Created")

    def urgency_indicator(self, obj):
        if not obj.created_at:
            return "Available after creation."

        now = timezone.now()
        age = now - obj.created_at

        if obj.status == OrderStatus.PENDING and age > timedelta(hours=24):
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "🚨 Urgent"
                "</span>"
            )
        elif obj.status == OrderStatus.PROCESSING and age > timedelta(days=3):
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                "⚠️ Delayed"
                "</span>"
            )
        elif obj.status == OrderStatus.SHIPPED and age > timedelta(days=7):
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded-full">'
                "📦 Follow up"
                "</span>"
            )
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

        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium {} {} rounded-full gap-1">'
            "<span>{}</span>"
            "<span>{}</span>"
            "</span>",
            config["bg"],
            config["text"],
            config["icon"],
            obj.get_status_display(),
        )

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

        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium {} {} rounded-full gap-1">'
            "<span>{}</span>"
            "<span>{}</span>"
            "</span>",
            config["bg"],
            config["text"],
            config["icon"],
            obj.get_payment_status_display(),
        )

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
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium {} {} rounded-full gap-1">'
            "<span>{}</span>"
            "<span>{}</span>"
            "</span>",
            config["bg"],
            config["text"],
            config["icon"],
            obj.get_document_type_display(),
        )

    document_type_badge.short_description = _("Document Type")

    def currency_status(self, obj):
        try:
            items_currency = obj.total_price_items.currency
            shipping_currency = obj.shipping_price.currency

            if items_currency == shipping_currency:
                return format_html(
                    '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                    "✅ {}"
                    "</span>",
                    items_currency,
                )
            else:
                return format_html(
                    '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                    "⚠️ Mixed"
                    "</span>"
                )
        except ValueError:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "❌ Mismatch"
                "</span>"
            )

    currency_status.short_description = _("Currency")

    def customer_summary(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Full Name:</strong></div><div>{}</div>"
            "<div><strong>Email:</strong></div><div>{}</div>"
            "<div><strong>Phone:</strong></div><div>{}</div>"
            "<div><strong>Mobile:</strong></div><div>{}</div>"
            "<div><strong>Account:</strong></div><div>{}</div>"
            "</div>"
            "</div>",
            obj.customer_full_name,
            obj.email,
            obj.phone or "Not provided",
            obj.mobile_phone or "Not provided",
            "Registered User" if obj.user else "Guest",
        )

    customer_summary.short_description = _("Customer Summary")

    def financial_summary(self, obj):
        shipping = obj.shipping_price
        items_total = obj.total_price_items

        try:
            total = obj.total_price
            total_display = str(total)
            currency_warning = ""
        except ValueError as e:
            total_display = format_html(
                '<span class="text-red-600 dark:text-red-400">Currency Mismatch</span>'
            )
            currency_warning = format_html(
                '<div><strong>Currency Issue:</strong></div><div class="text-red-600 dark:text-red-400 text-xs">{}</div>',
                str(e),
            )

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Items Total:</strong></div><div>{}</div>"
            "<div><strong>Shipping:</strong></div><div>{}</div>"
            '<div><strong>Grand Total:</strong></div><div class="font-bold">{}</div>'
            "<div><strong>Paid Amount:</strong></div><div>{}</div>"
            "<div><strong>Payment Status:</strong></div><div>{}</div>"
            "<div><strong>Payment Method:</strong></div><div>{}</div>"
            "<div><strong>Document Type:</strong></div><div>{}</div>"
            "{}"
            "</div>"
            "</div>",
            items_total,
            shipping,
            total_display,
            obj.paid_amount or "Not paid",
            obj.get_payment_status_display(),
            obj.payment_method or "Not specified",
            obj.get_document_type_display(),
            currency_warning,
        )

    financial_summary.short_description = _("Financial Summary")

    def shipping_summary(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Full Address:</strong></div><div>{}</div>"
            "<div><strong>Tracking:</strong></div><div>{}</div>"
            "<div><strong>Carrier:</strong></div><div>{}</div>"
            "<div><strong>Shipping Cost:</strong></div><div>{}</div>"
            "</div>"
            "</div>",
            obj.full_address,
            obj.tracking_number or "Not assigned",
            obj.shipping_carrier or "Not assigned",
            obj.shipping_price,
        )

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

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Order Age:</strong></div><div>{}d {}h</div>"
            "<div><strong>Status Age:</strong></div><div>{}</div>"
            "<div><strong>Items Count:</strong></div><div>{}</div>"
            "<div><strong>Can Cancel:</strong></div><div>{}</div>"
            "<div><strong>Is Paid:</strong></div><div>{}</div>"
            '<div><strong>Currency Status:</strong></div><div class="{}">{}</div>'
            '<div><strong>Available Docs:</strong></div><div class="text-xs">{}</div>'
            "</div>"
            "</div>",
            age.days,
            age.seconds // 3600,
            processing_time or "N/A",
            getattr(obj, "item_count", 0),
            "Yes" if obj.can_be_canceled else "No",
            "Yes" if obj.is_paid else "No",
            "text-red-600 dark:text-red-400"
            if "Mismatch" in currency_status or "Mixed" in currency_status
            else "",
            currency_status,
            doc_types,
        )

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
        url = f"/admin/order/order/{obj.order.id}/change/"
        status_badge = self.order_status_mini(obj.order)
        return format_html(
            '<div class="text-sm">'
            '<a href="{}" class="font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300">Order #{}</a>'
            "<div>{}</div>"
            "</div>",
            url,
            obj.order.id,
            status_badge,
        )

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
        return format_html(
            '<span class="{}">{}</span>', color, order.get_status_display()
        )

    def product_display(self, obj):
        product_name = (
            obj.product.safe_translation_getter("name", any_language=True)
            or "Unnamed Product"
        )
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="text-base-500 dark:text-base-400">ID: {}</div>'
            "</div>",
            product_name,
            obj.product.id,
        )

    product_display.short_description = _("Product")

    def quantity_display(self, obj):
        if obj.refunded_quantity > 0:
            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-base-900 dark:text-base-100">Total: {}</div>'
                '<div class="text-red-600 dark:text-red-400">Refunded: {}</div>'
                '<div class="text-green-600 dark:text-green-400">Net: {}</div>'
                "</div>",
                obj.quantity,
                obj.refunded_quantity,
                obj.net_quantity,
            )
        else:
            return format_html(
                '<span class="inline-flex items-center px-3 py-1 text-sm font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">'
                "x{}"
                "</span>",
                obj.quantity,
            )

    quantity_display.short_description = _("Quantity")

    def pricing_info(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{} each</div>'
            '<div class="font-bold text-base-900 dark:text-base-100">Total: {}</div>'
            '<div class="text-green-600 dark:text-green-400">Net: {}</div>'
            "</div>",
            obj.price,
            obj.total_price,
            obj.net_price,
        )

    pricing_info.short_description = _("Pricing")

    def refund_status_display(self, obj):
        if obj.is_refunded:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "↩️ Fully Refunded"
                "</span>"
            )
        elif obj.refunded_quantity > 0:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                "⚠️ Partial ({}/{})"
                "</span>",
                obj.refunded_quantity,
                obj.quantity,
            )
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
            "✅ Active"
            "</span>"
        )

    refund_status_display.short_description = _("Refund Status")

    def item_analytics(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Original Qty:</strong></div><div>{}</div>"
            "<div><strong>Current Qty:</strong></div><div>{}</div>"
            "<div><strong>Refunded Qty:</strong></div><div>{}</div>"
            "<div><strong>Net Qty:</strong></div><div>{}</div>"
            "<div><strong>Refunded Amount:</strong></div><div>{}</div>"
            "<div><strong>Net Amount:</strong></div><div>{}</div>"
            "</div>"
            "</div>",
            obj.original_quantity or obj.quantity,
            obj.quantity,
            obj.refunded_quantity,
            obj.net_quantity,
            obj.refunded_amount,
            obj.net_price,
        )

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
        url = f"/admin/order/order/{obj.order.id}/change/"
        return format_html(
            '<a href="{}" class="font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300">Order #{}</a>',
            url,
            obj.order.id,
        )

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

        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium {} {} rounded-full gap-1">'
            "<span>{}</span>"
            "<span>{}</span>"
            "</span>",
            config["bg"],
            config["text"],
            config["icon"],
            obj.get_change_type_display(),
        )

    change_type_badge.short_description = _("Change Type")

    def description_display(self, obj):
        description = (
            obj.safe_translation_getter("description", any_language=True)
            or "No description"
        )
        return format_html(
            '<div class="text-sm text-base-700 dark:text-base-300" title="{}">{}</div>',
            description,
            description[:80] + "..." if len(description) > 80 else description,
        )

    description_display.short_description = _("Description")

    def user_display(self, obj):
        if obj.user:
            return format_html(
                '<div class="text-sm text-base-700 dark:text-base-300">{}</div>',
                obj.user.full_name or obj.user.username,
            )
        return format_html(
            '<span class="text-base-400 dark:text-base-500 italic">System</span>'
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
        url = f"/admin/order/orderitem/{obj.order_item.id}/change/"
        return format_html(
            '<div class="text-sm">'
            '<a href="{}" class="font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300">Item #{}</a>'
            '<div class="text-base-500 dark:text-base-400">Order #{}</div>'
            "</div>",
            url,
            obj.order_item.id,
            obj.order_item.order.id,
        )

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

        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium {} {} rounded-full gap-1">'
            "<span>{}</span>"
            "<span>{}</span>"
            "</span>",
            config["bg"],
            config["text"],
            config["icon"],
            obj.get_change_type_display(),
        )

    change_type_badge.short_description = _("Change Type")

    def description_display(self, obj):
        description = (
            obj.safe_translation_getter("description", any_language=True)
            or "No description"
        )
        return format_html(
            '<div class="text-sm text-base-700 dark:text-base-300">{}</div>',
            description[:60] + "..." if len(description) > 60 else description,
        )

    description_display.short_description = _("Description")

    def user_display(self, obj):
        if obj.user:
            return format_html(
                '<div class="text-sm text-base-700 dark:text-base-300">{}</div>',
                obj.user.full_name or obj.user.username,
            )
        return format_html(
            '<span class="text-base-400 dark:text-base-500 italic">System</span>'
        )

    user_display.short_description = _("Changed By")
