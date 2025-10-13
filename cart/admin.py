from datetime import timedelta

from django.contrib import admin
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
            return queryset.filter(user__isnull=True)
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
            safe_name = conditional_escape(product_name)
            safe_id = conditional_escape(str(obj.product.id))
            html = (
                f'<div class="text-sm">'
                f'<div class="font-medium text-base-900 dark:text-base-100">{safe_name}</div>'
                f'<div class="text-base-500 dark:text-base-400">ID: {safe_id}</div>'
                f"</div>"
            )
            return mark_safe(html)
        return "-"

    product_display.short_description = _("Product")

    def unit_price_display(self, obj):
        if hasattr(obj, "price") and hasattr(obj, "final_price"):
            if obj.price != obj.final_price:
                safe_price = conditional_escape(str(obj.price))
                safe_final = conditional_escape(str(obj.final_price))
                html = (
                    f'<div class="text-sm">'
                    f'<div class="line-through text-base-500 dark:text-base-400">{safe_price}</div>'
                    f'<div class="font-medium text-green-600 dark:text-green-400">{safe_final}</div>'
                    f"</div>"
                )
            else:
                safe_final = conditional_escape(str(obj.final_price))
                html = (
                    f'<div class="text-sm font-medium text-base-900 dark:text-base-100">'
                    f"{safe_final}"
                    f"</div>"
                )
            return mark_safe(html)
        return "-"

    unit_price_display.short_description = _("Unit Price")

    def total_price_display(self, obj):
        if hasattr(obj, "total_price"):
            safe_total = conditional_escape(str(obj.total_price))
            html = (
                f'<div class="text-sm font-bold text-base-900 dark:text-base-100">'
                f"{safe_total}"
                f"</div>"
            )
            return mark_safe(html)
        return "-"

    total_price_display.short_description = _("Total")

    def discount_info(self, obj):
        if hasattr(obj, "discount_percent") and obj.discount_percent > 0:
            safe_percent = conditional_escape(str(obj.discount_percent))
            html = (
                f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                f'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                f"-{safe_percent}%"
                f"</span>"
            )
            return mark_safe(html)
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
        "user__email",
        "user__username",
        "user__first_name",
        "user__last_name",
    )
    date_hierarchy = "last_activity"
    list_select_related = ["user"]
    readonly_fields = [
        "id",
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
                "fields": ("user",),
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
        return super().get_queryset(request).prefetch_related("items__product")

    def cart_owner_display(self, obj):
        if obj.user:
            display_name = obj.user.full_name or obj.user.username
            safe_name = conditional_escape(display_name)
            safe_email = conditional_escape(obj.user.email)
            html = (
                f'<div class="text-sm">'
                f'<div class="font-medium text-base-900 dark:text-base-100">{safe_name}</div>'
                f'<div class="text-base-500 dark:text-base-400">{safe_email}</div>'
                f"</div>"
            )
            return mark_safe(html)
        else:
            return mark_safe(
                '<div class="text-sm">'
                '<div class="font-medium text-base-700 dark:text-base-300">Guest User</div>'
                f'<div class="text-base-500 dark:text-base-400">Cart #{obj.id}</div>'
                "</div>"
            )

    cart_owner_display.short_description = _("Cart Owner")

    def cart_type_badge(self, obj):
        if obj.user:
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                "👤 User"
                "</span>"
            )
        else:
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">'
                "🛒 Guest"
                "</span>"
            )

    cart_type_badge.short_description = _("Type")

    def activity_status_badge(self, obj):
        now = timezone.now()
        delta = now - obj.last_activity
        if delta < timedelta(hours=1):
            label, emoji = "Active", "🟢"
            bg, text = (
                "bg-green-50 dark:bg-green-900",
                "text-green-700 dark:text-green-300",
            )
        elif delta < timedelta(hours=24):
            label, emoji = "Recent", "🟡"
            bg, text = (
                "bg-yellow-50 dark:bg-yellow-900",
                "text-yellow-700 dark:text-yellow-300",
            )
        elif delta < timedelta(days=7):
            label, emoji = "Idle", "🟠"
            bg, text = (
                "bg-orange-50 dark:bg-orange-900",
                "text-orange-700 dark:text-orange-300",
            )
        else:
            label, emoji = "Abandoned", "🔴"
            bg, text = (
                "bg-red-50 dark:bg-red-900",
                "text-red-700 dark:text-red-300",
            )

        html = (
            f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium {bg} {text} rounded-full">'
            f"{emoji} {conditional_escape(label)}"
            f"</span>"
        )
        return mark_safe(html)

    activity_status_badge.short_description = _("Activity")

    def items_summary(self, obj):
        safe_total = conditional_escape(str(obj.total_items))
        safe_unique = conditional_escape(str(obj.total_items_unique))
        html = (
            f'<div class="text-sm text-base-700 dark:text-base-300">'
            f'<div class="font-medium">{safe_total} items</div>'
            f'<div class="text-base-500 dark:text-base-400">{safe_unique} unique</div>'
            f"</div>"
        )
        return mark_safe(html)

    items_summary.short_description = _("Items")

    def price_summary(self, obj):
        total_price = obj.total_price
        total_discount = obj.total_discount_value

        safe_price = conditional_escape(str(total_price))
        if getattr(total_discount, "amount", 0) > 0:
            safe_disc = conditional_escape(str(total_discount))
            html = (
                f'<div class="text-sm">'
                f'<div class="font-bold text-base-900 dark:text-base-100">{safe_price}</div>'
                f'<div class="text-red-600 dark:text-red-400 text-xs">-{safe_disc} saved</div>'
                f"</div>"
            )
        else:
            html = (
                f'<div class="text-sm font-bold text-base-900 dark:text-base-100">'
                f"{safe_price}"
                f"</div>"
            )
        return mark_safe(html)

    price_summary.short_description = _("Total")

    def cart_summary(self, obj):
        safe_items = conditional_escape(str(obj.total_items))
        safe_unique = conditional_escape(str(obj.total_items_unique))
        safe_price = conditional_escape(str(obj.total_price))
        safe_disc = conditional_escape(str(obj.total_discount_value))
        safe_vat = conditional_escape(str(obj.total_vat_value))
        safe_last = conditional_escape(
            obj.last_activity.strftime("%Y-%m-%d %H:%M")
        )
        safe_created = conditional_escape(
            obj.created_at.strftime("%Y-%m-%d %H:%M")
        )

        html = (
            f'<div class="grid grid-cols-2 gap-4 text-sm">'
            f"<div>"
            f"<strong>Items:</strong> {safe_items} total, {safe_unique} unique<br>"
            f"<strong>Total Price:</strong> {safe_price}<br>"
            f"<strong>Total Discount:</strong> {safe_disc}"
            f"</div>"
            f"<div>"
            f"<strong>VAT:</strong> {safe_vat}<br>"
            f"<strong>Activity:</strong> {safe_last}<br>"
            f"<strong>Created:</strong> {safe_created}"
            f"</div>"
            f"</div>"
        )
        return mark_safe(html)

    cart_summary.short_description = _("Cart Summary")

    def financial_summary(self, obj):
        savings_percent = 0.0
        if (
            getattr(obj.total_price, "amount", 0) > 0
            and getattr(obj.total_discount_value, "amount", 0) > 0
        ):
            original = obj.total_price.amount + obj.total_discount_value.amount
            savings_percent = (obj.total_discount_value.amount / original) * 100

        safe_final = conditional_escape(str(obj.total_price))
        safe_disc = conditional_escape(str(obj.total_discount_value))
        safe_vat = conditional_escape(str(obj.total_vat_value))
        safe_savings = conditional_escape(f"{savings_percent:.1f}%")

        html = (
            f'<div class="text-sm">'
            f'<div class="mb-2"><strong>Financial Breakdown:</strong></div>'
            f'<div class="grid grid-cols-2 gap-2">'
            f'<div>Final Total:</div><div class="font-bold">{safe_final}</div>'
            f'<div>Total Discounts:</div><div class="text-red-600 dark:text-red-400">-{safe_disc}</div>'
            f"<div>Total VAT:</div><div>{safe_vat}</div>"
            f'<div>Savings:</div><div class="text-green-600 dark:text-green-400">{safe_savings}</div>'
            f"</div>"
            f"</div>"
        )
        return mark_safe(html)

    financial_summary.short_description = _("Financial Summary")


@admin.register(CartItem)
class CartItemAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    list_display = (
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
        owner = (
            "Guest"
            if not obj.cart.user
            else obj.cart.user.full_name or obj.cart.user.username
        )
        safe_owner = conditional_escape(owner)
        safe_cart = conditional_escape(str(obj.cart.id))
        html = (
            f'<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">Cart #{safe_cart}</div>'
            f'<div class="text-base-500 dark:text-base-400">{safe_owner}</div>'
            f"</div>"
        )
        return mark_safe(html)

    cart_info.short_description = _("Cart")

    def product_display(self, obj):
        product_name = (
            obj.product.safe_translation_getter("name", any_language=True)
            or "Unnamed Product"
        )
        safe_name = conditional_escape(product_name)
        safe_id = conditional_escape(str(obj.product.id))
        html = (
            f'<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_name}</div>'
            f'<div class="text-base-500 dark:text-base-400">ID: {safe_id}</div>'
            f"</div>"
        )
        return mark_safe(html)

    product_display.short_description = _("Product")

    def quantity_display(self, obj):
        safe_qty = conditional_escape(str(obj.quantity))
        html = (
            f'<span class="inline-flex items-center px-3 py-1 text-sm font-medium '
            f'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">'
            f"x{safe_qty}"
            f"</span>"
        )
        return mark_safe(html)

    quantity_display.short_description = _("Qty")

    def pricing_info(self, obj):
        if (
            hasattr(obj, "price")
            and hasattr(obj, "final_price")
            and hasattr(obj, "total_price")
        ):
            if obj.price != obj.final_price:
                safe_price = conditional_escape(str(obj.price))
                safe_final = conditional_escape(str(obj.final_price))
                safe_total = conditional_escape(str(obj.total_price))
                html = (
                    f'<div class="text-sm">'
                    f'<div class="text-base-500 dark:text-base-400 line-through">{safe_price} each</div>'
                    f'<div class="font-medium text-green-600 dark:text-green-400">{safe_final} each</div>'
                    f'<div class="font-bold text-base-900 dark:text-base-100">Total: {safe_total}</div>'
                    f"</div>"
                )
            else:
                safe_final = conditional_escape(str(obj.final_price))
                safe_total = conditional_escape(str(obj.total_price))
                html = (
                    f'<div class="text-sm">'
                    f'<div class="font-medium text-base-900 dark:text-base-100">{safe_final} each</div>'
                    f'<div class="font-bold text-base-900 dark:text-base-100">Total: {safe_total}</div>'
                    f"</div>"
                )
            return mark_safe(html)
        return "-"

    pricing_info.short_description = _("Pricing")

    def discount_badge(self, obj):
        if hasattr(obj, "discount_percent") and obj.discount_percent > 0:
            safe_percent = conditional_escape(str(obj.discount_percent))
            html = (
                f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                f'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                f"🏷️ -{safe_percent}%"
                f"</span>"
            )
            return mark_safe(html)
        return ""

    discount_badge.short_description = _("Discount")

    def pricing_breakdown(self, obj):
        safe_price = conditional_escape(str(getattr(obj, "price", "N/A")))
        safe_final = conditional_escape(str(getattr(obj, "final_price", "N/A")))
        safe_qty = conditional_escape(str(obj.quantity))
        safe_total = conditional_escape(str(getattr(obj, "total_price", "N/A")))
        html = (
            f'<div class="text-sm">'
            f'<div class="grid grid-cols-2 gap-2">'
            f"<div>Unit Price:</div><div>{safe_price}</div>"
            f'<div>Final Price:</div><div class="font-medium">{safe_final}</div>'
            f"<div>Quantity:</div><div>{safe_qty}</div>"
            f'<div>Total:</div><div class="font-bold">{safe_total}</div>'
            f"</div>"
            f"</div>"
        )
        return mark_safe(html)

    pricing_breakdown.short_description = _("Pricing Breakdown")

    def savings_info(self, obj):
        if hasattr(obj, "discount_percent") and obj.discount_percent > 0:
            safe_percent = conditional_escape(
                str(getattr(obj, "discount_percent", 0))
            )
            safe_value = conditional_escape(
                str(getattr(obj, "discount_value", "N/A"))
            )
            safe_total = conditional_escape(
                str(getattr(obj, "total_discount_value", "N/A"))
            )
            html = (
                f'<div class="text-sm text-green-600 dark:text-green-400">'
                f"<div>Discount: {safe_percent}%</div>"
                f"<div>You save: {safe_value} per item</div>"
                f"<div>Total savings: {safe_total}</div>"
                f"</div>"
            )
            return mark_safe(html)
        return mark_safe(
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
