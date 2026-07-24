from datetime import timedelta

from django.contrib import admin
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.admin import TabularInline
from unfold.contrib.filters.admin import (
    AutocompleteSelectFilter,
    DropdownFilter,
    RangeDateTimeFilter,
    RangeNumericListFilter,
    RelatedDropdownFilter,
    SliderNumericFilter,
)
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from admin.base import BaseModelAdmin
from admin.displays import format_dt, header_two_line, money
from cart.models import Cart, CartItem

CART_TYPE_VARIANT: dict[str, str] = {
    "user": "success",
    "guest": "default",
}

ACTIVITY_STATE_VARIANT: dict[str, str] = {
    "active": "success",
    "recent": "info",
    "idle": "warning",
    "abandoned": "danger",
}


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
    per_page = 15
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
        if not obj.product:
            return "-"
        name = obj.product.safe_translation_getter(
            "name", any_language=True
        ) or _("Unnamed Product")
        return f"{name} (ID: {obj.product.id})"

    @admin.display(description=_("Unit Price"))
    def unit_price_display(self, obj):
        if not (hasattr(obj, "price") and hasattr(obj, "final_price")):
            return "-"
        if obj.price != obj.final_price:
            return _("%(price)s → %(final)s") % {
                "price": money(obj.price.amount),
                "final": money(obj.final_price.amount),
            }
        return money(obj.final_price.amount)

    @admin.display(description=_("Total"))
    def total_price_display(self, obj):
        if not hasattr(obj, "total_price"):
            return "-"
        return money(obj.total_price.amount)

    @admin.display(description=_("Discount"))
    def discount_info(self, obj):
        if hasattr(obj, "discount_percent") and obj.discount_percent > 0:
            return f"-{obj.discount_percent}%"
        return ""


@admin.register(Cart)
class CartAdmin(BaseModelAdmin):
    list_display = (
        "cart_owner_display",
        "cart_type",
        "activity_state",
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

    @display(description=_("Owner"), header=True)
    def cart_owner_display(self, obj):
        if obj.user:
            return header_two_line(
                obj.user.full_name or obj.user.username, obj.user.email
            )
        return header_two_line(str(_("Guest")), f"Cart #{obj.id}")

    @display(description=_("Type"), label=CART_TYPE_VARIANT)
    def cart_type(self, obj):
        if obj.user:
            return "user", _("User")
        return "guest", _("Guest")

    @display(description=_("Activity"), label=ACTIVITY_STATE_VARIANT)
    def activity_state(self, obj):
        delta = timezone.now() - obj.last_activity
        if delta < timedelta(hours=1):
            return "active", _("Active")
        if delta < timedelta(hours=24):
            return "recent", _("Recent")
        if delta < timedelta(days=7):
            return "idle", _("Idle")
        return "abandoned", _("Abandoned")

    @admin.display(description=_("Items"))
    def items_summary(self, obj):
        return _("%(total)d items, %(unique)d unique") % {
            "total": obj.total_items,
            "unique": obj.total_items_unique,
        }

    @admin.display(description=_("Total"))
    def price_summary(self, obj):
        total_discount = obj.total_discount_value
        if getattr(total_discount, "amount", 0) > 0:
            return _("%(total)s (-%(discount)s saved)") % {
                "total": money(obj.total_price.amount),
                "discount": money(total_discount.amount),
            }
        return money(obj.total_price.amount)

    @admin.display(description=_("Cart Summary"))
    def cart_summary(self, obj):
        return _(
            "Items: %(items)d total, %(unique)d unique. Total price: "
            "%(price)s. Total discount: %(disc)s. VAT: %(vat)s. "
            "Last activity: %(last)s. Created: %(created)s."
        ) % {
            "items": obj.total_items,
            "unique": obj.total_items_unique,
            "price": money(obj.total_price.amount),
            "disc": money(obj.total_discount_value.amount),
            "vat": money(obj.total_vat_value.amount),
            "last": format_dt(obj.last_activity),
            "created": format_dt(obj.created_at),
        }

    @admin.display(description=_("Financial Summary"))
    def financial_summary(self, obj):
        savings_percent = 0.0
        if (
            getattr(obj.total_price, "amount", 0) > 0
            and getattr(obj.total_discount_value, "amount", 0) > 0
        ):
            original = obj.total_price.amount + obj.total_discount_value.amount
            savings_percent = (obj.total_discount_value.amount / original) * 100

        return _(
            "Final total: %(final)s. Discounts: -%(disc)s. VAT: "
            "%(vat)s. Savings: %(savings).1f%%."
        ) % {
            "final": money(obj.total_price.amount),
            "disc": money(obj.total_discount_value.amount),
            "vat": money(obj.total_vat_value.amount),
            "savings": savings_percent,
        }


@admin.register(CartItem)
class CartItemAdmin(BaseModelAdmin):
    date_hierarchy = "created_at"

    list_display = (
        "cart_info",
        "product_display",
        "quantity_display",
        "pricing_info",
        "discount_display",
        "created_at",
    )
    list_filter = (
        # AutocompleteSelectFilter populates the dropdown lazily via
        # /admin/autocomplete/ XHR only when the admin user types — it
        # does NOT pre-fetch every Cart / Product row on changelist
        # load. RelatedDropdownFilter previously fetched all rows and
        # rendered ``str(obj)`` for each, triggering ``Cart.__str__`` →
        # ``cart.items.all()`` (×197) and ``Product`` →
        # ``product.translations`` (×151) per-option chains — the
        # bulk of the 368-query / 3s wall cost on /admin/cart/cartitem/.
        # Requires search_fields on CartAdmin + ProductAdmin (both
        # already defined).
        ("cart", AutocompleteSelectFilter),
        ("product", AutocompleteSelectFilter),
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
            _("Guest")
            if not obj.cart.user
            else obj.cart.user.full_name or obj.cart.user.username
        )
        return f"Cart #{obj.cart.id} — {owner}"

    @admin.display(description=_("Product"))
    def product_display(self, obj):
        name = obj.product.safe_translation_getter(
            "name", any_language=True
        ) or _("Unnamed Product")
        return f"{name} (ID: {obj.product.id})"

    @admin.display(description=_("Qty"))
    def quantity_display(self, obj):
        return f"x{obj.quantity}"

    @admin.display(description=_("Pricing"))
    def pricing_info(self, obj):
        if not (
            hasattr(obj, "price")
            and hasattr(obj, "final_price")
            and hasattr(obj, "total_price")
        ):
            return "-"
        if obj.price != obj.final_price:
            return _("%(price)s → %(final)s each — total %(total)s") % {
                "price": money(obj.price.amount),
                "final": money(obj.final_price.amount),
                "total": money(obj.total_price.amount),
            }
        return _("%(final)s each — total %(total)s") % {
            "final": money(obj.final_price.amount),
            "total": money(obj.total_price.amount),
        }

    @admin.display(description=_("Discount"))
    def discount_display(self, obj):
        if hasattr(obj, "discount_percent") and obj.discount_percent > 0:
            return f"-{obj.discount_percent}%"
        return ""

    @admin.display(description=_("Pricing Breakdown"))
    def pricing_breakdown(self, obj):
        return _(
            "Unit price: %(price)s. Final price: %(final)s. "
            "Quantity: %(qty)s. Total: %(total)s."
        ) % {
            "price": money(obj.price.amount),
            "final": money(obj.final_price.amount),
            "qty": obj.quantity,
            "total": money(obj.total_price.amount),
        }

    @admin.display(description=_("Savings"))
    def savings_info(self, obj):
        if obj.discount_percent > 0:
            return _(
                "Discount: %(percent)s%%. You save %(value)s per item "
                "(%(total)s total)."
            ) % {
                "percent": obj.discount_percent,
                "value": money(obj.discount_value.amount),
                "total": money(obj.total_discount_value.amount),
            }
        return _("No discounts applied")

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
