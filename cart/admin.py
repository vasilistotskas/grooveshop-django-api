from datetime import timedelta

from django.contrib import admin
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

from cart.models import Cart, CartItem


class CartTypeFilter(DropdownFilter):
    title = _("Cart Type")
    parameter_name = "cart_type"

    def lookups(self, request, model_admin):
        return [
            ("authenticated", _("Authenticated Users")),
            ("guest", _("Guest Users")),
            ("abandoned", _("Abandoned Carts")),
        ]

    def queryset(self, request, queryset):
        if self.value() == "authenticated":
            return queryset.filter(user__isnull=False)
        elif self.value() == "guest":
            return queryset.filter(user__isnull=True, session_key__isnull=False)
        elif self.value() == "abandoned":
            cutoff = timezone.now() - timedelta(hours=24)
            return queryset.filter(last_activity__lt=cutoff)
        return queryset


class TotalItemsFilter(RangeNumericListFilter):
    title = _("Total Items")
    parameter_name = "total_items"

    def queryset(self, request, queryset):
        filters = {}

        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["items__quantity__sum__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["items__quantity__sum__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class ActivityStatusFilter(DropdownFilter):
    title = _("Activity Status")
    parameter_name = "activity_status"

    def lookups(self, request, model_admin):
        return [
            ("active", _("Active (Last 24h)")),
            ("recent", _("Recent (Last 7 days)")),
            ("old", _("Old (7+ days)")),
        ]

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == "active":
            return queryset.filter(last_activity__gte=now - timedelta(hours=24))
        elif self.value() == "recent":
            return queryset.filter(
                last_activity__gte=now - timedelta(days=7),
                last_activity__lt=now - timedelta(hours=24),
            )
        elif self.value() == "old":
            return queryset.filter(last_activity__lt=now - timedelta(days=7))
        return queryset


class CartItemInline(TabularInline):
    model = CartItem
    extra = 0
    fields = (
        "product_display",
        "quantity",
        "unit_price_display",
        "total_price_display",
        "discount_info",
    )
    readonly_fields = (
        "product_display",
        "unit_price_display",
        "total_price_display",
        "discount_info",
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

    def unit_price_display(self, obj):
        if hasattr(obj, "price") and hasattr(obj, "final_price"):
            if obj.price != obj.final_price:
                return format_html(
                    '<div class="text-sm">'
                    '<div class="line-through text-base-500 dark:text-base-400">{}</div>'
                    '<div class="font-medium text-green-600 dark:text-green-400">{}</div>'
                    "</div>",
                    obj.price,
                    obj.final_price,
                )
            else:
                return format_html(
                    '<div class="text-sm font-medium text-base-900 dark:text-base-100">{}</div>',
                    obj.final_price,
                )
        return "-"

    unit_price_display.short_description = _("Unit Price")

    def total_price_display(self, obj):
        if hasattr(obj, "total_price"):
            return format_html(
                '<div class="text-sm font-bold text-base-900 dark:text-base-100">{}</div>',
                obj.total_price,
            )
        return "-"

    total_price_display.short_description = _("Total")

    def discount_info(self, obj):
        if hasattr(obj, "discount_percent") and obj.discount_percent > 0:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "-{}%"
                "</span>",
                obj.discount_percent,
            )
        return ""

    discount_info.short_description = _("Discount")


@admin.register(Cart)
class CartAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    list_display = (
        "id",
        "cart_owner_display",
        "cart_type_badge",
        "activity_status_badge",
        "items_summary",
        "price_summary",
        "last_activity",
        "created_at",
    )
    list_filter = (
        CartTypeFilter,
        ActivityStatusFilter,
        ("last_activity", RangeDateTimeFilter),
        ("created_at", RangeDateTimeFilter),
        ("user", RelatedDropdownFilter),
        TotalItemsFilter,
    )
    search_fields = (
        "id",
        "session_key",
        "user__email",
        "user__username",
        "user__first_name",
        "user__last_name",
    )
    date_hierarchy = "last_activity"
    list_select_related = ["user"]
    readonly_fields = [
        "id",
        "session_key",
        "created_at",
        "updated_at",
        "last_activity",
        "cart_summary",
        "financial_summary",
    ]

    fieldsets = (
        (
            _("Cart Owner"),
            {
                "fields": ("user", "session_key"),
                "classes": ("wide",),
            },
        ),
        (
            _("Activity"),
            {
                "fields": ("last_activity", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
        (
            _("Summary"),
            {
                "fields": ("cart_summary", "financial_summary"),
                "classes": ("collapse",),
            },
        ),
    )

    inlines = [CartItemInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related("items__product")

    def cart_owner_display(self, obj):
        if obj.user:
            full_name = obj.user.full_name
            username = obj.user.username
            email = obj.user.email

            display_name = full_name if full_name else username
            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
                '<div class="text-base-500 dark:text-base-400">{}</div>'
                "</div>",
                display_name,
                email,
            )
        elif obj.session_key:
            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-base-700 dark:text-base-300">Guest User</div>'
                '<div class="text-base-500 dark:text-base-400 font-mono">{}...</div>'
                "</div>",
                obj.session_key[:12],
            )
        return format_html(
            '<span class="text-base-400 dark:text-base-500 italic">Anonymous</span>'
        )

    cart_owner_display.short_description = _("Cart Owner")

    def cart_type_badge(self, obj):
        if obj.user:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                "👤 User"
                "</span>"
            )
        elif obj.session_key:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">'
                "🛒 Guest"
                "</span>"
            )
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-gray-50 dark:bg-gray-800 text-base-700 dark:text-base-300 rounded-full">'
            "❓ Unknown"
            "</span>"
        )

    cart_type_badge.short_description = _("Type")

    def activity_status_badge(self, obj):
        now = timezone.now()
        time_diff = now - obj.last_activity

        if time_diff < timedelta(hours=1):
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                "🟢 Active"
                "</span>"
            )
        elif time_diff < timedelta(hours=24):
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded-full">'
                "🟡 Recent"
                "</span>"
            )
        elif time_diff < timedelta(days=7):
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                "🟠 Idle"
                "</span>"
            )
        else:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "🔴 Abandoned"
                "</span>"
            )

    activity_status_badge.short_description = _("Activity")

    def items_summary(self, obj):
        total_items = obj.total_items
        unique_items = obj.total_items_unique

        return format_html(
            '<div class="text-sm text-base-700 dark:text-base-300">'
            '<div class="font-medium">{} items</div>'
            '<div class="text-base-500 dark:text-base-400">{} unique</div>'
            "</div>",
            total_items,
            unique_items,
        )

    items_summary.short_description = _("Items")

    def price_summary(self, obj):
        total_price = obj.total_price
        total_discount = obj.total_discount_value

        if total_discount.amount > 0:
            return format_html(
                '<div class="text-sm">'
                '<div class="font-bold text-base-900 dark:text-base-100">{}</div>'
                '<div class="text-red-600 dark:text-red-400 text-xs">-{} saved</div>'
                "</div>",
                total_price,
                total_discount,
            )
        else:
            return format_html(
                '<div class="text-sm font-bold text-base-900 dark:text-base-100">{}</div>',
                total_price,
            )

    price_summary.short_description = _("Total")

    def cart_summary(self, obj):
        return format_html(
            '<div class="grid grid-cols-2 gap-4 text-sm">'
            "<div>"
            "<strong>Items:</strong> {} total, {} unique<br>"
            "<strong>Total Price:</strong> {}<br>"
            "<strong>Total Discount:</strong> {}"
            "</div>"
            "<div>"
            "<strong>VAT:</strong> {}<br>"
            "<strong>Activity:</strong> {}<br>"
            "<strong>Created:</strong> {}"
            "</div>"
            "</div>",
            obj.total_items,
            obj.total_items_unique,
            obj.total_price,
            obj.total_discount_value,
            obj.total_vat_value,
            obj.last_activity.strftime("%Y-%m-%d %H:%M"),
            obj.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    cart_summary.short_description = _("Cart Summary")

    def financial_summary(self, obj):
        savings_percent = 0
        if obj.total_price.amount > 0 and obj.total_discount_value.amount > 0:
            original_total = (
                obj.total_price.amount + obj.total_discount_value.amount
            )
            savings_percent = (
                obj.total_discount_value.amount / original_total
            ) * 100

        return format_html(
            '<div class="text-sm">'
            '<div class="mb-2"><strong>Financial Breakdown:</strong></div>'
            '<div class="grid grid-cols-2 gap-2">'
            '<div>Final Total:</div><div class="font-bold">{}</div>'
            '<div>Total Discounts:</div><div class="text-red-600 dark:text-red-400">-{}</div>'
            "<div>Total VAT:</div><div>{}</div>"
            '<div>Savings:</div><div class="text-green-600 dark:text-green-400">{:.1f}%</div>'
            "</div>"
            "</div>",
            obj.total_price,
            obj.total_discount_value,
            obj.total_vat_value,
            savings_percent,
        )

    financial_summary.short_description = _("Financial Summary")


@admin.register(CartItem)
class CartItemAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    list_display = (
        "id",
        "cart_info",
        "product_display",
        "quantity_display",
        "pricing_info",
        "discount_badge",
        "created_at",
    )
    list_filter = (
        ("cart", RelatedDropdownFilter),
        ("product", RelatedDropdownFilter),
        ("quantity", SliderNumericFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = (
        "cart__id",
        "cart__user__email",
        "cart__user__username",
        "product__translations__name",
        "cart__session_key",
    )
    list_select_related = ["cart", "cart__user", "product"]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "pricing_breakdown",
        "savings_info",
    ]

    fieldsets = (
        (
            _("Cart Item"),
            {
                "fields": ("cart", "product", "quantity"),
                "classes": ("wide",),
            },
        ),
        (
            _("Pricing Information"),
            {
                "fields": ("pricing_breakdown", "savings_info"),
                "classes": ("collapse",),
            },
        ),
        (
            _("System"),
            {
                "fields": ("id", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    actions = [
        "increase_quantity",
        "decrease_quantity",
        "remove_from_cart",
    ]

    def cart_info(self, obj):
        owner_info = (
            "Guest"
            if not obj.cart.user
            else obj.cart.user.full_name or obj.cart.user.username
        )
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">Cart #{}</div>'
            '<div class="text-base-500 dark:text-base-400">{}</div>'
            "</div>",
            obj.cart.id,
            owner_info,
        )

    cart_info.short_description = _("Cart")

    def product_display(self, obj):
        product_name = (
            obj.product.safe_translation_getter("name", any_language=True)
            or "Unnamed Product"
        )
        product_id = obj.product.id

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="text-base-500 dark:text-base-400">ID: {}</div>'
            "</div>",
            product_name,
            product_id,
        )

    product_display.short_description = _("Product")

    def quantity_display(self, obj):
        return format_html(
            '<span class="inline-flex items-center px-3 py-1 text-sm font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">'
            "x{}"
            "</span>",
            obj.quantity,
        )

    quantity_display.short_description = _("Qty")

    def pricing_info(self, obj):
        if (
            hasattr(obj, "price")
            and hasattr(obj, "final_price")
            and hasattr(obj, "total_price")
        ):
            if obj.price != obj.final_price:
                return format_html(
                    '<div class="text-sm">'
                    '<div class="text-base-500 dark:text-base-400 line-through">{} each</div>'
                    '<div class="font-medium text-green-600 dark:text-green-400">{} each</div>'
                    '<div class="font-bold text-base-900 dark:text-base-100">Total: {}</div>'
                    "</div>",
                    obj.price,
                    obj.final_price,
                    obj.total_price,
                )
            else:
                return format_html(
                    '<div class="text-sm">'
                    '<div class="font-medium text-base-900 dark:text-base-100">{} each</div>'
                    '<div class="font-bold text-base-900 dark:text-base-100">Total: {}</div>'
                    "</div>",
                    obj.final_price,
                    obj.total_price,
                )
        return "-"

    pricing_info.short_description = _("Pricing")

    def discount_badge(self, obj):
        if hasattr(obj, "discount_percent") and obj.discount_percent > 0:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "🏷️ -{}%"
                "</span>",
                obj.discount_percent,
            )
        return ""

    discount_badge.short_description = _("Discount")

    def pricing_breakdown(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div>Unit Price:</div><div>{}</div>"
            '<div>Final Price:</div><div class="font-medium">{}</div>'
            "<div>Quantity:</div><div>{}</div>"
            '<div>Total:</div><div class="font-bold">{}</div>'
            "</div>"
            "</div>",
            getattr(obj, "price", "N/A"),
            getattr(obj, "final_price", "N/A"),
            obj.quantity,
            getattr(obj, "total_price", "N/A"),
        )

    pricing_breakdown.short_description = _("Pricing Breakdown")

    def savings_info(self, obj):
        if hasattr(obj, "discount_percent") and obj.discount_percent > 0:
            return format_html(
                '<div class="text-sm text-green-600 dark:text-green-400">'
                "<div>Discount: {}%</div>"
                "<div>You save: {} per item</div>"
                "<div>Total savings: {}</div>"
                "</div>",
                getattr(obj, "discount_percent", 0),
                getattr(obj, "discount_value", "N/A"),
                getattr(obj, "total_discount_value", "N/A"),
            )
        return format_html(
            '<div class="text-sm text-base-500 dark:text-base-400">No discounts applied</div>'
        )

    savings_info.short_description = _("Savings")

    @action(
        description=_("Increase quantity by 1"),
        variant=ActionVariant.PRIMARY,
        icon="add",
    )
    def increase_quantity(self, request, queryset):
        for item in queryset:
            item.quantity += 1
            item.save()
        self.message_user(
            request,
            _("Quantity increased for %(count)d items.")
            % {"count": queryset.count()},
        )

    @action(
        description=_("Decrease quantity by 1"),
        variant=ActionVariant.WARNING,
        icon="remove",
    )
    def decrease_quantity(self, request, queryset):
        for item in queryset:
            if item.quantity > 1:
                item.quantity -= 1
                item.save()
            else:
                item.delete()
        self.message_user(
            request,
            _("Quantity decreased for %(count)d items.")
            % {"count": queryset.count()},
        )

    @action(
        description=_("Remove from cart"),
        variant=ActionVariant.DANGER,
        icon="delete",
    )
    def remove_from_cart(self, request, queryset):
        count = queryset.count()
        queryset.delete()
        self.message_user(
            request,
            _("%(count)d items were removed from carts.") % {"count": count},
        )
