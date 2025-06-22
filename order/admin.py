from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin

from order.enum.status import OrderStatus
from order.models.item import OrderItem
from order.models.order import Order
from order.services import OrderService


class OrderItemLine(admin.TabularInline):
    model = OrderItem
    readonly_fields = ("product", "price", "quantity", "total_price")
    can_delete = False
    extra = 0
    fields = ("product", "price", "quantity", "total_price")
    show_change_link = True

    def total_price(self, obj):
        return obj.total_price


@admin.register(Order)
class OrderAdmin(ModelAdmin):
    list_display = [
        "id",
        "status_badge",
        "customer_name",
        "email",
        "phone",
        "city",
        "created_display",
        "paid_amount",
        "payment_status",
        "item_count",
    ]
    list_filter = [
        "status",
        "payment_status",
        "created_at",
        "country",
        "payment_method",
        "document_type",
    ]
    search_fields = [
        "email",
        "id",
        "uuid",
        "first_name",
        "last_name",
        "phone",
        "mobile_phone",
        "city",
        "tracking_number",
    ]
    readonly_fields = (
        "uuid",
        "user",
        "first_name",
        "last_name",
        "email",
        "zipcode",
        "place",
        "phone",
        "mobile_phone",
        "created_at",
        "updated_at",
        "status_updated_at",
        "paid_amount",
        "customer_notes",
        "city",
        "full_address",
        "payment_id",
        "customer_full_name",
        "total_price_items",
        "total_price_extra",
    )
    fieldsets = (
        (
            "Order Information",
            {
                "fields": (
                    "uuid",
                    "status",
                    "document_type",
                    "created_at",
                    "updated_at",
                    "status_updated_at",
                )
            },
        ),
        (
            "Customer Information",
            {
                "fields": (
                    "user",
                    "customer_full_name",
                    "email",
                    "phone",
                    "mobile_phone",
                )
            },
        ),
        (
            "Address",
            {
                "fields": (
                    "country",
                    "region",
                    "city",
                    "zipcode",
                    "street",
                    "street_number",
                    "floor",
                    "location_type",
                    "place",
                    "full_address",
                )
            },
        ),
        (
            "Payment",
            {
                "fields": (
                    "pay_way",
                    "payment_status",
                    "payment_method",
                    "payment_id",
                    "paid_amount",
                    "shipping_price",
                    "total_price_items",
                    "total_price_extra",
                )
            },
        ),
        (
            "Shipping",
            {
                "fields": (
                    "tracking_number",
                    "shipping_carrier",
                )
            },
        ),
        ("Notes", {"fields": ("customer_notes",)}),
    )
    actions = [
        "mark_as_processing",
        "mark_as_shipped",
        "mark_as_delivered",
        "mark_as_completed",
        "mark_as_canceled",
    ]
    inlines = [OrderItemLine]
    save_on_top = True

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(item_count=Count("items"))

    def customer_name(self, obj):
        return f"{obj.first_name} {obj.last_name}"

    customer_name.short_description = _("Customer Name")

    def created_display(self, obj):
        return obj.created_at.strftime("%Y-%m-%d %H:%M")

    created_display.short_description = _("Created")

    def item_count(self, obj):
        return obj.item_count

    item_count.short_description = _("Items")
    item_count.admin_order_field = "item_count"

    def status_badge(self, obj):
        status_colors = {
            OrderStatus.PENDING: "#FFA500",  # Orange
            OrderStatus.PROCESSING: "#1E90FF",  # Blue
            OrderStatus.SHIPPED: "#9370DB",  # Purple
            OrderStatus.DELIVERED: "#32CD32",  # Green
            OrderStatus.COMPLETED: "#228B22",  # Dark Green
            OrderStatus.CANCELED: "#DC143C",  # Red
            OrderStatus.RETURNED: "#FF4500",  # Orange Red
            OrderStatus.REFUNDED: "#B22222",  # Fire Brick
        }

        color = status_colors.get(obj.status, "#808080")  # Default to gray

        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 10px; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    status_badge.short_description = _("Status")

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

    mark_as_processing.short_description = _(
        "Mark selected orders as processing"
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

    mark_as_shipped.short_description = _("Mark selected orders as shipped")

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

    mark_as_delivered.short_description = _("Mark selected orders as delivered")

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

    mark_as_completed.short_description = _("Mark selected orders as completed")

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

    mark_as_canceled.short_description = _(
        "Cancel selected orders and restore stock"
    )


@admin.register(OrderItem)
class OrderItemAdmin(ModelAdmin):
    list_display = [
        "id",
        "order_link",
        "product_name",
        "price",
        "quantity",
        "total_price",
    ]
    list_filter = ["order__status", "created_at"]
    search_fields = ["order__id", "product__translations__name", "product__id"]
    readonly_fields = ["order", "product", "price", "quantity", "total_price"]

    def order_link(self, obj):
        url = f"/admin/order/order/{obj.order.id}/change/"
        return format_html('<a href="{}">{}</a>', url, obj.order)

    order_link.short_description = _("Order")

    def product_name(self, obj):
        return obj.product.safe_translation_getter("name", any_language=True)

    product_name.short_description = _("Product")

    def total_price(self, obj):
        return obj.total_price

    total_price.short_description = _("Total")
