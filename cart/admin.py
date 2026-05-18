from datetime import timedelta

from django.contrib import admin
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.html import format_html
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

    @admin.display(description=_("Product"))
    def product_display(self, obj):
        if obj.product:
            product_name = (
                obj.product.safe_translation_getter("name", any_language=True)
                or "Unnamed Product"
            )
            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-base-900 dark:text-base-100">{name}</div>'
                '<div class="text-base-600 dark:text-base-300">ID: {id}</div>'
                "</div>",
                name=product_name,
                id=obj.product.id,
            )
        return "-"

    @admin.display(description=_("Unit Price"))
    def unit_price_display(self, obj):
        if hasattr(obj, "price") and hasattr(obj, "final_price"):
            if obj.price != obj.final_price:
                return format_html(
                    '<div class="text-sm">'
                    '<div class="line-through text-base-600 dark:text-base-300">{price}</div>'
                    '<div class="font-medium text-green-600 dark:text-green-400">{final}</div>'
                    "</div>",
                    price=str(obj.price),
                    final=str(obj.final_price),
                )
            return format_html(
                '<div class="text-sm font-medium text-base-900 dark:text-base-100">'
                "{final}"
                "</div>",
                final=str(obj.final_price),
            )
        return "-"

    @admin.display(description=_("Total"))
    def total_price_display(self, obj):
        if hasattr(obj, "total_price"):
            return format_html(
                '<div class="text-sm font-bold text-base-900 dark:text-base-100">'
                "{total}"
                "</div>",
                total=str(obj.total_price),
            )
        return "-"

    @admin.display(description=_("Discount"))
    def discount_info(self, obj):
        if hasattr(obj, "discount_percent") and obj.discount_percent > 0:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "-{percent}%"
                "</span>",
                percent=obj.discount_percent,
            )
        return ""


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
    raw_id_fields = ["user"]
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
        # ``price_summary`` reads ``cart.total_price`` → ``item.final_price``
        # → ``product.final_price`` → ``product.vat_value`` → ``product.vat``.
        # Without ``items__product__vat`` in the prefetch chain, every
        # cart-item product fetched its own VAT row, costing 89 queries
        # on a 12-cart page.
        return (
            super()
            .get_queryset(request)
            .prefetch_related("items__product__vat")
        )

    @admin.display(description=_("Cart Owner"))
    def cart_owner_display(self, obj):
        if obj.user:
            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-base-900 dark:text-base-100">{name}</div>'
                '<div class="text-base-600 dark:text-base-300">{email}</div>'
                "</div>",
                name=obj.user.full_name or obj.user.username,
                email=obj.user.email,
            )
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-700 dark:text-base-300">Guest User</div>'
            '<div class="text-base-600 dark:text-base-300">Cart #{id}</div>'
            "</div>",
            id=obj.id,
        )

    @admin.display(description=_("Type"))
    def cart_type_badge(self, obj):
        if obj.user:
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                "👤 User"
                "</span>"
            )
        return mark_safe(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">'
            "🛒 Guest"
            "</span>"
        )

    @admin.display(description=_("Activity"))
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

        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium {bg} {text_class} rounded-full">'
            "{emoji} {label}"
            "</span>",
            bg=bg,
            text_class=text,
            emoji=emoji,
            label=label,
        )

    @admin.display(description=_("Items"))
    def items_summary(self, obj):
        return format_html(
            '<div class="text-sm text-base-700 dark:text-base-300">'
            '<div class="font-medium">{total} items</div>'
            '<div class="text-base-600 dark:text-base-300">{unique} unique</div>'
            "</div>",
            total=obj.total_items,
            unique=obj.total_items_unique,
        )

    @admin.display(description=_("Total"))
    def price_summary(self, obj):
        total_price = obj.total_price
        total_discount = obj.total_discount_value

        if getattr(total_discount, "amount", 0) > 0:
            return format_html(
                '<div class="text-sm">'
                '<div class="font-bold text-base-900 dark:text-base-100">{price}</div>'
                '<div class="text-red-600 dark:text-red-400 text-xs">-{disc} saved</div>'
                "</div>",
                price=str(total_price),
                disc=str(total_discount),
            )
        return format_html(
            '<div class="text-sm font-bold text-base-900 dark:text-base-100">'
            "{price}"
            "</div>",
            price=str(total_price),
        )

    @admin.display(description=_("Cart Summary"))
    def cart_summary(self, obj):
        return format_html(
            '<div class="grid grid-cols-2 gap-4 text-sm">'
            "<div>"
            "<strong>Items:</strong> {items} total, {unique} unique<br>"
            "<strong>Total Price:</strong> {price}<br>"
            "<strong>Total Discount:</strong> {disc}"
            "</div>"
            "<div>"
            "<strong>VAT:</strong> {vat}<br>"
            "<strong>Activity:</strong> {last}<br>"
            "<strong>Created:</strong> {created}"
            "</div>"
            "</div>",
            items=obj.total_items,
            unique=obj.total_items_unique,
            price=str(obj.total_price),
            disc=str(obj.total_discount_value),
            vat=str(obj.total_vat_value),
            last=obj.last_activity.strftime("%Y-%m-%d %H:%M"),
            created=obj.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    @admin.display(description=_("Financial Summary"))
    def financial_summary(self, obj):
        savings_percent = 0.0
        if (
            getattr(obj.total_price, "amount", 0) > 0
            and getattr(obj.total_discount_value, "amount", 0) > 0
        ):
            original = obj.total_price.amount + obj.total_discount_value.amount
            savings_percent = (obj.total_discount_value.amount / original) * 100

        return format_html(
            '<div class="text-sm">'
            '<div class="mb-2"><strong>Financial Breakdown:</strong></div>'
            '<div class="grid grid-cols-2 gap-2">'
            '<div>Final Total:</div><div class="font-bold">{final}</div>'
            '<div>Total Discounts:</div><div class="text-red-600 dark:text-red-400">-{disc}</div>'
            "<div>Total VAT:</div><div>{vat}</div>"
            '<div>Savings:</div><div class="text-green-600 dark:text-green-400">{savings}</div>'
            "</div>"
            "</div>",
            final=str(obj.total_price),
            disc=str(obj.total_discount_value),
            vat=str(obj.total_vat_value),
            savings=f"{savings_percent:.1f}%",
        )


@admin.register(CartItem)
class CartItemAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True
    date_hierarchy = "created_at"

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

    def get_queryset(self, request):
        # ``product_display`` calls ``safe_translation_getter`` and
        # ``pricing_info`` reads ``obj.final_price`` → which walks
        # ``product.final_price → product.vat_value → product.vat``.
        # Without these, every row fires two extra queries — costing
        # 441 queries (766ms SQL, ~3s wall) on a typical changelist
        # page. ``product__vat`` is a FK chain so ``select_related``
        # (single join on the main query) is cheaper than prefetch.
        # Translations are a reverse relation, so prefetch is the
        # only option. Mirrors the ``CartAdmin`` fix in ``c18d45b9``.
        return (
            super()
            .get_queryset(request)
            .select_related("cart", "cart__user", "product", "product__vat")
            .prefetch_related("product__translations")
        )

    @admin.display(description=_("Cart"))
    def cart_info(self, obj):
        owner = (
            "Guest"
            if not obj.cart.user
            else obj.cart.user.full_name or obj.cart.user.username
        )
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">Cart #{cart}</div>'
            '<div class="text-base-600 dark:text-base-300">{owner}</div>'
            "</div>",
            cart=obj.cart.id,
            owner=owner,
        )

    @admin.display(description=_("Product"))
    def product_display(self, obj):
        product_name = (
            obj.product.safe_translation_getter("name", any_language=True)
            or "Unnamed Product"
        )
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{name}</div>'
            '<div class="text-base-600 dark:text-base-300">ID: {id}</div>'
            "</div>",
            name=product_name,
            id=obj.product.id,
        )

    @admin.display(description=_("Qty"))
    def quantity_display(self, obj):
        return format_html(
            '<span class="inline-flex items-center px-3 py-1 text-sm font-medium '
            'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">'
            "x{qty}"
            "</span>",
            qty=obj.quantity,
        )

    @admin.display(description=_("Pricing"))
    def pricing_info(self, obj):
        if (
            hasattr(obj, "price")
            and hasattr(obj, "final_price")
            and hasattr(obj, "total_price")
        ):
            if obj.price != obj.final_price:
                return format_html(
                    '<div class="text-sm">'
                    '<div class="text-base-600 dark:text-base-300 line-through">{price} each</div>'
                    '<div class="font-medium text-green-600 dark:text-green-400">{final} each</div>'
                    '<div class="font-bold text-base-900 dark:text-base-100">Total: {total}</div>'
                    "</div>",
                    price=str(obj.price),
                    final=str(obj.final_price),
                    total=str(obj.total_price),
                )
            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-base-900 dark:text-base-100">{final} each</div>'
                '<div class="font-bold text-base-900 dark:text-base-100">Total: {total}</div>'
                "</div>",
                final=str(obj.final_price),
                total=str(obj.total_price),
            )
        return "-"

    @admin.display(description=_("Discount"))
    def discount_badge(self, obj):
        if hasattr(obj, "discount_percent") and obj.discount_percent > 0:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "🏷️ -{percent}%"
                "</span>",
                percent=obj.discount_percent,
            )
        return ""

    @admin.display(description=_("Pricing Breakdown"))
    def pricing_breakdown(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div>Unit Price:</div><div>{price}</div>"
            '<div>Final Price:</div><div class="font-medium">{final}</div>'
            "<div>Quantity:</div><div>{qty}</div>"
            '<div>Total:</div><div class="font-bold">{total}</div>'
            "</div>"
            "</div>",
            price=str(getattr(obj, "price", "N/A")),
            final=str(getattr(obj, "final_price", "N/A")),
            qty=obj.quantity,
            total=str(getattr(obj, "total_price", "N/A")),
        )

    @admin.display(description=_("Savings"))
    def savings_info(self, obj):
        if hasattr(obj, "discount_percent") and obj.discount_percent > 0:
            return format_html(
                '<div class="text-sm text-green-600 dark:text-green-400">'
                "<div>Discount: {percent}%</div>"
                "<div>You save: {value} per item</div>"
                "<div>Total savings: {total}</div>"
                "</div>",
                percent=getattr(obj, "discount_percent", 0),
                value=str(getattr(obj, "discount_value", "N/A")),
                total=str(getattr(obj, "total_discount_value", "N/A")),
            )
        return mark_safe(
            '<div class="text-sm text-base-600 dark:text-base-300">No discounts applied</div>'
        )

    @action(
        description=str(_("Increase quantity by 1")),
        variant=ActionVariant.PRIMARY,
        icon="add",
    )
    def increase_quantity(self, request, queryset):
        count = queryset.count()
        with transaction.atomic():
            queryset.update(quantity=F("quantity") + 1)
        self.message_user(
            request,
            _("Quantity increased for %(count)d items.") % {"count": count},
        )

    @action(
        description=str(_("Decrease quantity by 1")),
        variant=ActionVariant.WARNING,
        icon="remove",
    )
    def decrease_quantity(self, request, queryset):
        count = queryset.count()
        with transaction.atomic():
            # Delete items that are already at quantity 1 (would reach 0)
            queryset.filter(quantity__lte=1).delete()
            # Decrease the rest
            queryset.filter(quantity__gt=1).update(quantity=F("quantity") - 1)
        self.message_user(
            request,
            _("Quantity decreased for %(count)d items.") % {"count": count},
        )

    @action(
        description=str(_("Remove from cart")),
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
