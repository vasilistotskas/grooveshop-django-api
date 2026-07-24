from datetime import timedelta
from decimal import Decimal

import admin_thumbnails
from django.contrib import admin, messages
from django.db.models import F, Q, Sum, Count, Prefetch
from django.db.models.functions import TruncDay
from django.http import HttpResponseRedirect, Http404
from django.shortcuts import redirect, render
from django.urls import path, reverse, reverse_lazy
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from parler.admin import TranslatableAdmin
from simple_history.admin import SimpleHistoryAdmin
from unfold.admin import TabularInline

from admin.base import BaseModelAdmin, BaseTranslatableAdmin
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
    RangeNumericFilter,
    RangeNumericListFilter,
    RelatedDropdownFilter,
    SliderNumericFilter,
)
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from admin.displays import (
    REVIEW_STATUS_VARIANT,
    choice_label,
    format_dt,
    header_two_line,
    money,
    relative_time,
)
from admin.export import ExportActionMixin
from core.forms.measurement import MeasurementWidget
from core.units import WeightUnits
from product.enum.category import CategoryImageTypeEnum
from product.enum.review import ReviewStatus
from product.forms import ApplyDiscountForm
from product.models.attribute import Attribute
from product.models.attribute_value import AttributeValue
from product.models.brand import Brand
from product.models.category import ProductCategory
from product.models.category_image import ProductCategoryImage
from product.models.favourite import ProductFavourite
from product.models.image import ProductImage
from product.models.product import Product
from product.models.product_attribute import ProductAttribute
from product.models.review import ProductReview
from product.models.variant_group import ProductVariantGroup
from tag.admin import TaggedItemInline

# ── Local (single-app) TextChoices/synthetic-status variant maps ──────
# Stock status and reservation status are derived states (not backed
# by a TextChoices field), and category image type only appears in
# this app — kept local rather than promoted to admin.displays.

STOCK_STATUS_VARIANT: dict[str, str] = {
    "out": "danger",
    "critical": "warning",
    "low": "warning",
    "in_stock": "success",
}

STOCK_RESERVATION_STATUS_VARIANT: dict[str, str] = {
    "consumed": "info",
    "pending": "warning",
}

CATEGORY_IMAGE_TYPE_VARIANT: dict[str, str] = {
    CategoryImageTypeEnum.MAIN: "primary",
    CategoryImageTypeEnum.BANNER: "info",
    CategoryImageTypeEnum.HERO: "info",
    CategoryImageTypeEnum.FEATURE: "warning",
    CategoryImageTypeEnum.PROMOTIONAL: "warning",
    CategoryImageTypeEnum.SEASONAL: "success",
    CategoryImageTypeEnum.ICON: "default",
    CategoryImageTypeEnum.THUMBNAIL: "default",
    CategoryImageTypeEnum.GALLERY: "default",
    CategoryImageTypeEnum.BACKGROUND: "default",
}


class StockStatusFilter(DropdownFilter):
    title = _("Stock Status")
    parameter_name = "stock_status"

    def lookups(self, request, model_admin):
        return [
            ("in_stock", _("In Stock (>0)")),
            ("low_stock", _("Low Stock (1-10)")),
            ("out_of_stock", _("Out of Stock (0)")),
            ("high_stock", _("High Stock (>50)")),
            ("critical_stock", _("Critical Stock (1-5)")),
        ]

    def queryset(self, request, queryset):
        filter_value = self.value()

        match filter_value:
            case "in_stock":
                filter_kwargs = {"stock__gt": 0}
            case "low_stock":
                filter_kwargs = {"stock__range": (1, 10)}
            case "out_of_stock":
                filter_kwargs = {"stock": 0}
            case "high_stock":
                filter_kwargs = {"stock__gt": 50}
            case "critical_stock":
                filter_kwargs = {"stock__range": (1, 5)}
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


class PriceRangeFilter(DropdownFilter):
    title = _("Price Range")
    parameter_name = "price_range"

    def lookups(self, request, model_admin):
        return [
            ("budget", _("Budget (€0-€20)")),
            ("affordable", _("Affordable (€20-€50)")),
            ("mid_range", _("Mid-range (€50-€100)")),
            ("premium", _("Premium (€100-€500)")),
            ("luxury", _("Luxury (€500+)")),
        ]

    def queryset(self, request, queryset):
        filter_value = self.value()

        match filter_value:
            case "budget":
                filter_kwargs = {"price__lte": 20}
            case "affordable":
                filter_kwargs = {"price__range": (20, 50)}
            case "mid_range":
                filter_kwargs = {"price__range": (50, 100)}
            case "premium":
                filter_kwargs = {"price__range": (100, 500)}
            case "luxury":
                filter_kwargs = {"price__gt": 500}
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


class DiscountStatusFilter(DropdownFilter):
    title = _("Discount Status")
    parameter_name = "discount_status"

    def lookups(self, request, model_admin):
        return [
            ("on_sale", _("On Sale (>0%)")),
            ("heavy_discount", _("Heavy Discount (>20%)")),
            ("clearance", _("Clearance (>50%)")),
            ("no_discount", _("No Discount (0%)")),
        ]

    def queryset(self, request, queryset):
        filter_value = self.value()

        match filter_value:
            case "on_sale":
                filter_kwargs = {"discount_percent__gt": 0}
            case "heavy_discount":
                filter_kwargs = {"discount_percent__gt": 20}
            case "clearance":
                filter_kwargs = {"discount_percent__gt": 50}
            case "no_discount":
                filter_kwargs = {"discount_percent": 0}
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


class PopularityFilter(DropdownFilter):
    title = _("Popularity")
    parameter_name = "popularity"

    def lookups(self, request, model_admin):
        return [
            ("trending", _("Trending (High Views)")),
            ("loved", _("Loved (High Likes)")),
            ("well_reviewed", _("Well Reviewed (>4.0)")),
            ("new_arrivals", _("New Arrivals (Last 30 days)")),
        ]

    def queryset(self, request, queryset):
        filter_value = self.value()

        match filter_value:
            case "trending":
                filter_kwargs = {"view_count__gt": 100}
            case "loved":
                queryset = queryset.with_likes_count()
                filter_kwargs = {"likes_count__gt": 10}
            case "well_reviewed":
                queryset = queryset.with_review_average()
                filter_kwargs = {"review_average__gt": 7.0}
            case "new_arrivals":
                thirty_days_ago = timezone.now() - timedelta(days=30)
                filter_kwargs = {"created_at__gte": thirty_days_ago}
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


class LikesCountFilter(RangeNumericListFilter):
    title = _("Likes count")
    parameter_name = "likes_count"

    def queryset(self, request, queryset):
        # Short-circuit when the filter is unused. Django admin
        # invokes every ``list_filter``'s ``queryset()`` on every
        # page load — without this guard ``with_likes_count()``
        # added a ``LEFT JOIN productfavourite`` + GROUP BY to the
        # main product fetch + the date-hierarchy DATE_TRUNC + the
        # COUNT query, costing ~2s extra per changelist load.
        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if not value_from and not value_to:
            return queryset

        queryset = queryset.with_likes_count()
        filters = {}
        if value_from:
            filters["likes_count__gte"] = value_from
        if value_to:
            filters["likes_count__lte"] = value_to
        return queryset.filter(**filters)

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class ReviewAverageFilter(RangeNumericListFilter):
    title = _("Review Rating")
    parameter_name = "review_average"

    def queryset(self, request, queryset):
        # Short-circuit when the filter is unused — same rationale
        # as ``LikesCountFilter`` above.
        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if not value_from and not value_to:
            return queryset

        queryset = queryset.with_review_average()
        filters = {}
        if value_from:
            filters["review_average__gte"] = value_from
        if value_to:
            filters["review_average__lte"] = value_to
        return queryset.filter(**filters)

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class StockReservationStatusFilter(DropdownFilter):
    """Filter products by stock reservation status."""

    title = _("Reservation Status")
    parameter_name = "reservation_status"

    def lookups(self, request, model_admin):
        return [
            ("has_reservations", _("Has Active Reservations")),
            ("no_reservations", _("No Active Reservations")),
            ("high_reservations", _("High Reservations (>50% stock)")),
        ]

    def queryset(self, request, queryset):
        filter_value = self.value()
        now = timezone.now()

        if filter_value == "has_reservations":
            # Products with active (non-expired, non-consumed) reservations
            return queryset.filter(
                stockreservation__expires_at__gt=now,
                stockreservation__consumed=False,
            ).distinct()
        elif filter_value == "no_reservations":
            # Products without any active reservations
            return queryset.exclude(
                stockreservation__expires_at__gt=now,
                stockreservation__consumed=False,
            )
        elif filter_value == "high_reservations":
            # Products where reserved quantity > 50% of stock
            # Annotate with reserved quantity and filter
            queryset = queryset.annotate(
                reserved_qty=Sum(
                    "stockreservation__quantity",
                    filter=Q(
                        stockreservation__expires_at__gt=now,
                        stockreservation__consumed=False,
                    ),
                )
            )
            return queryset.filter(reserved_qty__gt=F("stock") * 0.5)

        return queryset


class AttributeValueInline(TabularInline):
    """Inline for managing attribute values within attribute admin."""

    model = AttributeValue
    extra = 0
    fields = ("value_display", "active", "sort_order", "usage_count_display")
    readonly_fields = ("value_display", "usage_count_display")
    ordering_field = "sort_order"
    hide_ordering_field = True

    tab = True
    show_change_link = True

    @admin.display(description=_("Value"))
    def value_display(self, obj):
        if not obj.pk:
            return "-"
        return obj.safe_translation_getter("value", any_language=True) or _(
            "Unnamed"
        )

    @admin.display(description=_("Usage"))
    def usage_count_display(self, obj):
        if not obj.pk:
            return "-"
        return obj.product_attributes.count()


@admin.register(Attribute)
class AttributeAdmin(BaseTranslatableAdmin):
    """Admin interface for managing product attributes."""

    ordering_field = "sort_order"
    hide_ordering_field = True
    list_display = (
        "attribute_info",
        "active",
        "is_variant",
        "values_count_display",
        "usage_count_display",
        "sort_order",
        "created_display",
    )
    search_fields = [
        "id",
        "translations__name",
    ]
    list_filter = [
        "active",
        "is_variant",
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
    ]
    inlines = [AttributeValueInline]
    readonly_fields = (
        "id",
        "uuid",
        "created_at",
        "updated_at",
        "values_count_display",
        "usage_count_display",
    )
    actions = [
        "activate_attributes",
        "deactivate_attributes",
    ]
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Attribute Information"),
            {
                "fields": ("name", "active", "is_variant"),
                "classes": ("wide",),
            },
        ),
        (
            _("Statistics"),
            {
                "fields": ("values_count_display", "usage_count_display"),
                "classes": ("collapse",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": (
                    "id",
                    "uuid",
                    "sort_order",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        """Optimize queryset with annotations."""
        return (
            super()
            .get_queryset(request)
            .with_values_count()
            .with_usage_count()
            .prefetch_related("translations", "values")
        )

    @admin.display(description=_("Attribute"), ordering="translations__name")
    def attribute_info(self, obj):
        name = obj.safe_translation_getter("name", any_language=True) or _(
            "Unnamed"
        )
        return f"{name} (#{obj.id})"

    @display(description=_("Values"), ordering="values_count")
    def values_count_display(self, obj):
        return getattr(obj, "values_count", 0)

    @display(description=_("Usage"), ordering="usage_count")
    def usage_count_display(self, obj):
        return getattr(obj, "usage_count", 0)

    @display(description=_("Created"), ordering="created_at")
    def created_display(self, obj):
        return (
            f"{format_dt(obj.created_at, fmt='%d/%m/%Y')} "
            f"({relative_time(obj.created_at)})"
        )

    @action(
        description=str(_("Activate selected attributes")),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def activate_attributes(self, request, queryset):
        """Bulk action to activate selected attributes."""
        updated = queryset.update(active=True)
        self.message_user(
            request,
            ngettext(
                "%(count)d attribute was successfully activated.",
                "%(count)d attributes were successfully activated.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=str(_("Deactivate selected attributes")),
        variant=ActionVariant.WARNING,
        icon="cancel",
    )
    def deactivate_attributes(self, request, queryset):
        """Bulk action to deactivate selected attributes."""
        updated = queryset.update(active=False)
        self.message_user(
            request,
            ngettext(
                "%(count)d attribute was successfully deactivated.",
                "%(count)d attributes were successfully deactivated.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )


@admin.register(AttributeValue)
class AttributeValueAdmin(BaseTranslatableAdmin):
    """Admin interface for managing attribute values."""

    ordering_field = "sort_order"
    hide_ordering_field = True
    list_display = (
        "value_info",
        "attribute_display",
        "active",
        "usage_count_display",
        "sort_order",
        "created_display",
    )
    search_fields = [
        "id",
        "translations__value",
        "attribute__translations__name",
    ]
    list_filter = [
        "active",
        ("attribute", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
    ]
    readonly_fields = (
        "id",
        "uuid",
        "created_at",
        "updated_at",
        "usage_count_display",
    )
    list_select_related = ["attribute"]
    actions = [
        "activate_values",
        "deactivate_values",
    ]
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Value Information"),
            {
                "fields": ("attribute", "value", "active"),
                "classes": ("wide",),
            },
        ),
        (
            _("Statistics"),
            {
                "fields": ("usage_count_display",),
                "classes": ("collapse",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": (
                    "id",
                    "uuid",
                    "sort_order",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        """Optimize queryset with annotations."""
        return (
            super()
            .get_queryset(request)
            .with_usage_count()
            .select_related("attribute")
            .prefetch_related("translations", "attribute__translations")
        )

    @admin.display(description=_("Value"), ordering="translations__value")
    def value_info(self, obj):
        value = obj.safe_translation_getter("value", any_language=True) or _(
            "Unnamed"
        )
        return f"{value} (#{obj.id})"

    @admin.display(description=_("Attribute"))
    def attribute_display(self, obj):
        if not obj.attribute:
            return _("No Attribute")
        return obj.attribute.safe_translation_getter(
            "name", any_language=True
        ) or _("Unnamed")

    @display(description=_("Usage"), ordering="usage_count")
    def usage_count_display(self, obj):
        return getattr(obj, "usage_count", 0)

    @display(description=_("Created"), ordering="created_at")
    def created_display(self, obj):
        return (
            f"{format_dt(obj.created_at, fmt='%d/%m/%Y')} "
            f"({relative_time(obj.created_at)})"
        )

    @action(
        description=str(_("Activate selected values")),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def activate_values(self, request, queryset):
        """Bulk action to activate selected attribute values."""
        updated = queryset.update(active=True)
        self.message_user(
            request,
            ngettext(
                "%(count)d attribute value was successfully activated.",
                "%(count)d attribute values were successfully activated.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=str(_("Deactivate selected values")),
        variant=ActionVariant.WARNING,
        icon="cancel",
    )
    def deactivate_values(self, request, queryset):
        """Bulk action to deactivate selected attribute values."""
        updated = queryset.update(active=False)
        self.message_user(
            request,
            ngettext(
                "%(count)d attribute value was successfully deactivated.",
                "%(count)d attribute values were successfully deactivated.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )


class ProductAttributeInline(TabularInline):
    """Inline for managing product attributes within product admin."""

    model = ProductAttribute
    extra = 1
    fields = ("attribute_value",)
    autocomplete_fields = ["attribute_value"]

    tab = True
    verbose_name = _("Product Attribute")
    verbose_name_plural = _("Product Attributes")

    def get_queryset(self, request):
        """Optimize queryset with related data."""
        qs = super().get_queryset(request)
        return qs.select_related("attribute_value__attribute").prefetch_related(
            "attribute_value__translations",
            "attribute_value__attribute__translations",
        )


@admin_thumbnails.thumbnail("image")
class ProductImageInline(TabularInline):
    model = ProductImage
    extra = 0
    fields = ("image_thumbnail", "image", "is_main")

    tab = True
    show_change_link = True


class StockReservationInline(TabularInline):
    """Display active stock reservations for this product."""

    from order.models.stock_reservation import StockReservation

    model = StockReservation
    extra = 0
    can_delete = False

    fields = (
        "reservation_info",
        "quantity",
        "session_info",
        "expires_display",
        "status_display",
    )
    readonly_fields = fields

    tab = True
    verbose_name = _("Active Stock Reservation")
    verbose_name_plural = _("Active Stock Reservations")

    def get_queryset(self, request):
        """Only show active (non-expired, non-consumed) reservations."""
        qs = super().get_queryset(request)
        now = timezone.now()
        return (
            qs.filter(expires_at__gt=now, consumed=False)
            .select_related("reserved_by", "order")
            .order_by("-created_at")
        )

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Reservation"))
    def reservation_info(self, obj):
        return f"#{obj.id} — {format_dt(obj.created_at)}"

    @admin.display(description=_("Reserved By"))
    def session_info(self, obj):
        if obj.reserved_by:
            return obj.reserved_by.email or obj.reserved_by.username
        session_short = (
            obj.session_id[:8] if len(obj.session_id) > 8 else obj.session_id
        )
        return _("Guest: %(session)s…") % {"session": session_short}

    @admin.display(description=_("Expires In"))
    def expires_display(self, obj):
        minutes_left = int(
            (obj.expires_at - timezone.now()).total_seconds() / 60
        )
        return _("%(minutes)s min") % {"minutes": minutes_left}

    @display(description=_("Status"), label=STOCK_RESERVATION_STATUS_VARIANT)
    def status_display(self, obj):
        if obj.order_id:
            return "consumed", _("Order #%(id)s") % {"id": obj.order_id}
        return "pending", _("Pending checkout")


class StockLogInline(TabularInline):
    """Display recent stock operation history for this product."""

    from order.models.stock_log import StockLog

    model = StockLog
    extra = 0
    can_delete = False
    max_num = 20  # Limit to 20 records instead of slicing queryset

    fields = (
        "operation_display",
        "quantity_change",
        "stock_levels",
        "order_link",
        "performed_by_display",
        "timestamp_display",
    )
    readonly_fields = fields

    tab = True
    verbose_name = _("Stock Activity Log")
    verbose_name_plural = _("Stock Activity Logs (Recent 20)")

    def get_queryset(self, request):
        """Show last 20 stock operations, ordered by most recent."""
        qs = super().get_queryset(request)
        return qs.select_related("order", "performed_by").order_by(
            "-created_at"
        )

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Operation"))
    def operation_display(self, obj):
        return obj.get_operation_type_display()

    @admin.display(description=_("Change"))
    def quantity_change(self, obj):
        return f"{obj.quantity_delta:+d}"

    @admin.display(description=_("Stock Level"))
    def stock_levels(self, obj):
        return f"{obj.stock_before} -> {obj.stock_after}"

    @admin.display(description=_("Related Order"))
    def order_link(self, obj):
        if obj.order_id:
            return format_html(
                '<a href="{url}">Order #{id}</a>',
                url=reverse("admin:order_order_change", args=[obj.order_id]),
                id=obj.order_id,
            )
        return (obj.reason or "—")[:45]

    @admin.display(description=_("By"))
    def performed_by_display(self, obj):
        if obj.performed_by:
            return (obj.performed_by.email or obj.performed_by.username)[:20]
        return _("System")

    @admin.display(description=_("Time"))
    def timestamp_display(self, obj):
        return format_dt(obj.created_at, fmt="%d/%m %H:%M")


@admin.register(Product)
class ProductAdmin(
    TranslatableAdmin, ExportActionMixin, SimpleHistoryAdmin, BaseModelAdmin
):
    """Parler owns forms/urls (first in the MRO, matching
    ``BaseTranslatableAdmin``'s contract). ``ExportActionMixin`` only
    adds CSV/XML export actions (no cooperative-``super()`` methods
    of its own) and ``SimpleHistoryAdmin`` adds the audit-history
    view — neither redefines unfold's plumbing, so both sit between
    parler and ``BaseModelAdmin`` without breaking either chain. This
    is the same relative ordering the project used before the unfold
    conversion; only ``ExportModelAdmin`` (which re-extends unfold's
    ``ModelAdmin``, duplicating ``BaseModelAdmin``'s bases) was
    swapped for the plain ``ExportActionMixin``.
    """

    list_display = (
        "product_info",
        "category_display",
        "variant_group_display",
        "pricing_info",
        "stock_status",
        "active",
        "created_display",
    )
    search_fields = [
        "id",
        "sku",
        "translations__name",
        "translations__description",
        "category__translations__name",
        "brand__name",
    ]
    list_filter = [
        StockStatusFilter,
        StockReservationStatusFilter,
        PriceRangeFilter,
        DiscountStatusFilter,
        PopularityFilter,
        "active",
        ("category", RelatedDropdownFilter),
        ("variant_group", RelatedDropdownFilter),
        ("brand", RelatedDropdownFilter),
        ("vat", RelatedDropdownFilter),
        ("stock", RangeNumericFilter),
        ("price", RangeNumericFilter),
        ("discount_percent", SliderNumericFilter),
        ("view_count", RangeNumericFilter),
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
        LikesCountFilter,
        ReviewAverageFilter,
        "price_drop_alerts_enabled",
    ]
    inlines = [
        ProductAttributeInline,
        ProductImageInline,
        StockReservationInline,
        StockLogInline,
        TaggedItemInline,
    ]
    readonly_fields = (
        "id",
        "uuid",
        "sku",
        "created_at",
        "updated_at",
        "view_count",
        "likes_count",
        "stock_reservation_summary",
    )
    list_select_related = ["category", "vat", "brand", "changed_by"]
    autocomplete_fields = ["category", "vat", "variant_group", "brand"]
    search_help_text = _(
        "Search by ID, SKU, name, description, or category name."
    )
    actions = [
        "make_active",
        "make_inactive",
        "apply_custom_discount",
        "clear_discount",
    ]
    # Per-row quick action: clone a product into a new draft for the
    # catalog team to riff on without leaving the list page.
    actions_row = ["duplicate_product_row"]
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Product Information"),
            {
                "fields": (
                    "sku",
                    "category",
                    "variant_group",
                    "brand",
                    "active",
                    "name",
                    "description",
                ),
                "classes": ("tab",),
                "description": _(
                    "Assign a variant group to link this product with its "
                    "sibling variations (e.g. other colours). Flag the relevant "
                    "attributes (Colour, Memory) as variant axes under "
                    "Attributes for them to render as storefront selectors."
                ),
            },
        ),
        (
            _("Pricing & Inventory"),
            {
                "fields": (
                    "price",
                    "discount_percent",
                    "vat",
                    "stock",
                    "weight",
                    "low_stock_threshold",
                ),
                "classes": ("tab",),
            },
        ),
        (
            _("Loyalty Points"),
            {
                "fields": (
                    "points_coefficient",
                    "points",
                ),
                "classes": ("tab",),
                "description": _(
                    "Configure loyalty points earning for this product. "
                    "Points coefficient multiplies the global points factor, "
                    "and bonus points are awarded as a fixed amount on purchase."
                ),
            },
        ),
        (
            _("Customer Alerts"),
            {
                "fields": ("price_drop_alerts_enabled",),
                "classes": ("tab",),
                "description": _(
                    "Control which self-service alert features customers "
                    "can subscribe to for this product. Disabled by default "
                    "— admins opt products in per SKU."
                ),
            },
        ),
        (
            _("Stock Audit"),
            {
                "fields": ("stock_reservation_summary",),
                "classes": ("tab",),
                "description": _(
                    "View active stock reservations and recent stock operations for this product."
                ),
            },
        ),
        (
            _("SEO"),
            {
                "fields": (
                    "slug",
                    "seo_title",
                    "seo_description",
                    "seo_keywords",
                ),
                "classes": ("tab",),
            },
        ),
        (
            _("Performance"),
            {
                "fields": ("view_count", "likes_count"),
                "classes": ("tab",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": (
                    "id",
                    "uuid",
                    "created_at",
                    "updated_at",
                    "changed_by",
                ),
                "classes": ("tab",),
            },
        ),
    )

    def get_form(self, request, obj=None, change=False, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["weight"].widget = MeasurementWidget(
            unit_choices=WeightUnits.CHOICES
        )
        return form

    def get_queryset(self, request):
        from order.models.stock_reservation import StockReservation

        # Only apply expensive annotations when filtering/sorting requires
        # them. The ``else`` branches used to annotate unconditionally
        # "to avoid attribute errors", but that fired both joins
        # (``LEFT JOIN productfavourite`` + ``LEFT JOIN productreview``
        # + ``GROUP BY``) on every changelist load — a cartesian product
        # of favourites × reviews per product, costing 2.4s on the
        # main query and 875ms on the date-hierarchy DATE_TRUNC.
        # ``Product.likes_count`` / ``review_average`` properties
        # already fall back to per-row queries when no annotation is
        # present, so removing the unconditional annotation just shifts
        # the cost from "2.4s once" to "~1ms × 25 rows = ~25ms". These
        # two properties are no longer rendered as a list_display column
        # at all (dropped along with ``performance_metrics`` — see
        # ``PopularityFilter``/the "Performance" fieldset for where
        # views/likes/rating are still surfaced), but the conditional
        # annotation stays for the filter/ordering paths above.
        qs = super().get_queryset(request)

        # Check if we need likes_count or review_average based on filters/ordering
        needs_likes = (
            any("likes_count" in str(param) for param in request.GET.keys())
            or request.GET.get("o", "").find("likes") != -1
        )

        needs_reviews = (
            any("review_average" in str(param) for param in request.GET.keys())
            or request.GET.get("o", "").find("review") != -1
        )

        if needs_likes:
            qs = qs.with_likes_count()

        if needs_reviews:
            qs = qs.with_review_average()

        # Optimize related queries
        now = timezone.now()
        active_reservations = Prefetch(
            "stock_reservations",
            queryset=StockReservation.objects.filter(
                expires_at__gt=now, consumed=False
            ).only("id", "product_id", "quantity"),
            to_attr="active_reservations_list",
        )

        main_images = Prefetch(
            "images",
            queryset=ProductImage.objects.filter(is_main=True).only(
                "id", "product_id", "image", "is_main"
            ),
            to_attr="main_images_list",
        )

        # Sibling ids per group via prefetch (one cheap extra query) instead
        # of a Count() annotation — an unconditional multi-valued join here
        # would cartesian with the conditional likes/reviews annotations
        # above, the exact blowup this method exists to avoid.
        variant_siblings = Prefetch(
            "variant_group__variants",
            queryset=Product.objects.only("id", "variant_group_id"),
        )

        return (
            qs.select_related("category", "vat", "changed_by", "variant_group")
            .prefetch_related(
                main_images,
                active_reservations,
                variant_siblings,
                "variant_group__translations",
            )
            .only(
                # Only load fields we actually display
                "id",
                "sku",
                "active",
                "price",
                "price_currency",
                "discount_percent",
                "stock",
                "view_count",
                "created_at",
                "updated_at",
                "category_id",
                "vat_id",
                "changed_by_id",
                "variant_group_id",
            )
        )

    def get_prepopulated_fields(self, request, obj=None):
        return {"slug": ("name",)}

    @display(
        description=_("Product"), header=True, ordering="translations__name"
    )
    def product_info(self, obj):
        name = obj.safe_translation_getter("name", any_language=True) or _(
            "Untitled Product"
        )
        main_images = getattr(obj, "main_images_list", [])
        main_image = main_images[0] if main_images else None
        image_path = (
            main_image.image.url if main_image and main_image.image else None
        )
        return header_two_line(
            name, f"SKU {obj.sku[:8]}", image_path=image_path
        )

    @admin.display(
        description=_("Category"), ordering="category__translations__name"
    )
    def category_display(self, obj):
        if not obj.category:
            return "—"
        return obj.category.safe_translation_getter(
            "name", any_language=True
        ) or _("Unnamed")

    @admin.display(description=_("Variants"))
    def variant_group_display(self, obj):
        """Which variant family this product belongs to, linked to the
        group page, with the sibling count. Reads only prefetched data."""
        group = obj.variant_group
        if group is None:
            return "—"
        name = (
            group.safe_translation_getter("name", any_language=True)
            or f"Group #{group.pk}"
        )
        # len() over the prefetch cache — no per-row COUNT query.
        siblings = len(group.variants.all())
        return format_html(
            '<a href="{url}">{name}</a> ({count} {label})',
            url=reverse(
                "admin:product_productvariantgroup_change", args=[group.pk]
            ),
            name=name,
            count=siblings,
            label=_("variants"),
        )

    @admin.display(description=_("Pricing"), ordering="price")
    def pricing_info(self, obj):
        if obj.discount_percent > 0:
            return _("%(price)s -> %(final)s (-%(discount)s%%)") % {
                "price": money(obj.price.amount),
                "final": money(obj.final_price.amount),
                "discount": obj.discount_percent,
            }
        return money(obj.price.amount)

    @display(
        description=_("Stock"), label=STOCK_STATUS_VARIANT, ordering="stock"
    )
    def stock_status(self, obj):
        stock = obj.stock
        if stock == 0:
            return "out", _("Out of stock")
        if stock <= 5:
            return "critical", _("Critical (%(stock)s)") % {"stock": stock}
        if stock <= 10:
            return "low", _("Low (%(stock)s)") % {"stock": stock}
        return "in_stock", _("In stock (%(stock)s)") % {"stock": stock}

    @display(description=_("Created"), ordering="created_at")
    def created_display(self, obj):
        return (
            f"{format_dt(obj.created_at, fmt='%d/%m/%Y')} "
            f"({relative_time(obj.created_at)})"
        )

    @admin.display(description=_("Stock Reservation Summary"))
    def stock_reservation_summary(self, obj):
        """Active reservations + last-7-days stock ops, with a link to
        the full stock-history chart (``stock_history_view``)."""
        from order.models.stock_reservation import StockReservation
        from order.models.stock_log import StockLog

        now = timezone.now()

        active_reservations = StockReservation.objects.filter(
            product=obj, expires_at__gt=now, consumed=False
        )
        reservation_count = active_reservations.count()
        reserved_qty = (
            active_reservations.aggregate(total=Sum("quantity"))["total"] or 0
        )
        available_stock = obj.stock - reserved_qty

        seven_days_ago = now - timedelta(days=7)
        ops_by_type = dict(
            StockLog.objects.filter(product=obj, created_at__gte=seven_days_ago)
            .values_list("operation_type")
            .annotate(count=Count("id"))
        )

        history_url = reverse(
            "admin:product_product_stock_history", args=[obj.pk]
        )

        return format_html(
            "<p>{stock_line}</p><p>{ops_line}</p>"
            '<p><a href="{url}">{label}</a></p>',
            stock_line=_(
                "Stock %(stock)s — reserved %(reserved)s, available "
                "%(available)s across %(count)s active reservation(s)."
            )
            % {
                "stock": obj.stock,
                "reserved": reserved_qty,
                "available": available_stock,
                "count": reservation_count,
            },
            ops_line=_(
                "Last 7 days: %(reserve)s reserved, %(release)s released, "
                "%(decrement)s decremented, %(increment)s incremented."
            )
            % {
                "reserve": ops_by_type.get("RESERVE", 0),
                "release": ops_by_type.get("RELEASE", 0),
                "decrement": ops_by_type.get("DECREMENT", 0),
                "increment": ops_by_type.get("INCREMENT", 0),
            },
            url=history_url,
            label=_("Open full stock history chart"),
        )

    @action(
        description=str(_("Activate selected products")),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def make_active(self, request, queryset):
        updated = queryset.update(active=True)
        self.message_user(
            request,
            ngettext(
                "%(count)d product was successfully activated.",
                "%(count)d products were successfully activated.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=str(_("Deactivate selected products")),
        variant=ActionVariant.WARNING,
        icon="cancel",
    )
    def make_inactive(self, request, queryset):
        updated = queryset.update(active=False)
        self.message_user(
            request,
            ngettext(
                "%(count)d product was successfully deactivated.",
                "%(count)d products were successfully deactivated.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=str(_("Apply custom discount to selected products")),
        variant=ActionVariant.INFO,
        icon="local_offer",
        permissions=["change"],
    )
    def apply_custom_discount(self, request, queryset):
        is_action_post = "_selected_action" in request.POST
        is_form_post = "discount_percent" in request.POST

        if is_action_post and not is_form_post:
            selected_ids = list(queryset.values_list("id", flat=True))
            request.session["selected_product_ids"] = selected_ids

            total_count = queryset.count()
            active_count = queryset.filter(active=True).count()
            inactive_count = total_count - active_count

            form = ApplyDiscountForm()

            context = {
                **self.admin_site.each_context(request),
                "title": _("Apply Custom Discount"),
                "form": form,
                "queryset": queryset,
                "total_count": total_count,
                "active_count": active_count,
                "inactive_count": inactive_count,
                "opts": self.model._meta,
                "has_view_permission": self.has_view_permission(request),
                "has_change_permission": self.has_change_permission(request),
                "breadcrumbs_items": [
                    {"title": _("Home"), "link": "admin:index"},
                    {
                        "title": self.model._meta.verbose_name_plural.title(),
                        "link": "admin:product_product_changelist",
                    },
                    {"title": _("Apply Custom Discount")},
                ],
            }

            return render(request, "admin/product/apply_discount.html", context)

        if is_form_post:
            selected_ids = request.session.get("selected_product_ids", [])

            if selected_ids:
                queryset = Product.objects.filter(id__in=selected_ids)
            else:
                messages.error(
                    request, _("No products selected. Please try again.")
                )
                return HttpResponseRedirect(
                    reverse_lazy("admin:product_product_changelist")
                )

            form = ApplyDiscountForm(request.POST)

            if form.is_valid():
                discount_percent = form.cleaned_data["discount_percent"]
                apply_to_inactive = form.cleaned_data["apply_to_inactive"]

                if not apply_to_inactive:
                    queryset = queryset.filter(active=True)

                updated = queryset.update(discount_percent=discount_percent)

                if "selected_product_ids" in request.session:
                    del request.session["selected_product_ids"]

                self.message_user(
                    request,
                    ngettext(
                        "Applied %(discount)s%% discount to %(count)d product.",
                        "Applied %(discount)s%% discount to %(count)d products.",
                        updated,
                    )
                    % {"count": updated, "discount": discount_percent},
                    messages.SUCCESS,
                )

                return HttpResponseRedirect(
                    reverse_lazy("admin:product_product_changelist")
                )
            else:
                total_count = queryset.count()
                active_count = queryset.filter(active=True).count()
                inactive_count = total_count - active_count

                context = {
                    **self.admin_site.each_context(request),
                    "title": _("Apply Custom Discount"),
                    "form": form,
                    "queryset": queryset,
                    "total_count": total_count,
                    "active_count": active_count,
                    "inactive_count": inactive_count,
                    "opts": self.model._meta,
                    "has_view_permission": self.has_view_permission(request),
                    "has_change_permission": self.has_change_permission(
                        request
                    ),
                    "breadcrumbs_items": [
                        {"title": _("Home"), "link": "admin:index"},
                        {
                            "title": self.model._meta.verbose_name_plural.title(),
                            "link": "admin:product_product_changelist",
                        },
                        {"title": _("Apply Custom Discount")},
                    ],
                }

                return render(
                    request, "admin/product/apply_discount.html", context
                )

        return HttpResponseRedirect(
            reverse_lazy("admin:product_product_changelist")
        )

    @action(
        description=str(_("Clear discount from selected products")),
        variant=ActionVariant.DANGER,
        icon="cancel",
    )
    def clear_discount(self, request, queryset):
        updated = queryset.update(discount_percent=Decimal("0.0"))
        self.message_user(
            request,
            ngettext(
                "%(count)d product's discount was cleared.",
                "%(count)d products' discounts were cleared.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=str(_("Duplicate as draft")),
        icon="content_copy",
        variant=ActionVariant.INFO,
    )
    def duplicate_product_row(self, request, object_id):
        """Clone the row into an inactive draft and open it for editing.

        We deliberately do NOT copy translations, images, attributes,
        or stock — the catalog team uses the duplicate as a starting
        skeleton. ``slug`` and ``sku`` get a ``-copy-<n>`` suffix so
        the unique constraints hold; ``active=False`` keeps the draft
        out of the storefront until edits are finished.
        """

        try:
            original = Product.objects.get(pk=object_id)
        except Product.DoesNotExist:
            messages.error(request, _("Product not found."))
            return redirect("admin:product_product_changelist")

        clone = Product.objects.get(pk=object_id)
        clone.pk = None
        clone.id = None
        clone.uuid = None  # SoftDeleteModel/UUIDModel — regenerates
        clone.active = False
        clone.stock = 0
        clone.view_count = 0
        clone.low_stock_alert_sent = False

        # Append a numeric suffix to slug/sku until both are unique.
        n = 1
        base_slug = original.slug
        base_sku = getattr(original, "sku", "") or ""
        while True:
            candidate_slug = f"{base_slug}-copy-{n}"
            candidate_sku = f"{base_sku}-COPY-{n}" if base_sku else ""
            slug_taken = Product.objects.filter(slug=candidate_slug).exists()
            sku_taken = (
                bool(candidate_sku)
                and Product.objects.filter(sku=candidate_sku).exists()
            )
            if not slug_taken and not sku_taken:
                clone.slug = candidate_slug
                if candidate_sku:
                    clone.sku = candidate_sku
                break
            n += 1
            if n > 100:
                messages.error(
                    request,
                    _("Couldn't find a free slug — try renaming the source."),
                )
                return redirect(
                    "admin:product_product_change", object_id=object_id
                )

        clone.save()
        messages.success(
            request,
            _(
                "Cloned product #%(orig)s to draft #%(clone)s. Edit the copy "
                "and re-activate when ready."
            )
            % {"orig": original.id, "clone": clone.id},
        )
        return redirect("admin:product_product_change", object_id=clone.id)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:product_id>/stock-history/",
                self.admin_site.admin_view(self.stock_history_view),
                name="product_product_stock_history",
            ),
        ]
        return custom_urls + urls

    def stock_history_view(self, request, product_id):
        """Per-product stock history with a 90-day time-series chart.

        Renders a Chart.js stacked bar chart grouped by day and by
        StockLog.operation_type (RESERVE/RELEASE/DECREMENT/INCREMENT),
        plus a table of the most recent entries.
        """
        from order.models.stock_log import StockLog

        try:
            product = Product.objects.get(pk=product_id)
        except Product.DoesNotExist as exc:
            raise Http404(_("Product not found")) from exc

        try:
            window_days = int(request.GET.get("days") or 90)
        except (TypeError, ValueError):
            window_days = 90
        window_days = max(7, min(window_days, 365))
        since = timezone.now() - timedelta(days=window_days)

        rows = (
            StockLog.objects.filter(
                product_id=product_id, created_at__gte=since
            )
            .annotate(day=TruncDay("created_at"))
            .values("day", "operation_type")
            .annotate(total=Sum("quantity_delta"), entries=Count("id"))
            .order_by("day")
        )

        operation_types = ["RESERVE", "RELEASE", "DECREMENT", "INCREMENT"]
        buckets: dict[str, dict[str, int]] = {}
        for row in rows:
            day_key = row["day"].date().isoformat() if row["day"] else ""
            if not day_key:
                continue
            buckets.setdefault(day_key, {op: 0 for op in operation_types})[
                row["operation_type"]
            ] = int(row["total"] or 0)

        labels: list[str] = []
        cursor = since.date()
        end = timezone.now().date()
        while cursor <= end:
            labels.append(cursor.isoformat())
            cursor += timedelta(days=1)

        def _series(op):
            return [buckets.get(day, {}).get(op, 0) for day in labels]

        dataset_colors = {
            "RESERVE": "#f59e0b",
            "RELEASE": "#6366f1",
            "DECREMENT": "#ef4444",
            "INCREMENT": "#10b981",
        }
        datasets = [
            {
                "label": op.title(),
                "data": _series(op),
                "backgroundColor": dataset_colors[op],
                "stack": "stock-ops",
            }
            for op in operation_types
        ]

        recent_logs = (
            StockLog.objects.filter(product_id=product_id)
            .select_related("order", "performed_by")
            .order_by("-created_at")[:50]
        )

        product_name = (
            product.safe_translation_getter("name", any_language=True)
            or f"Product #{product.id}"
        )

        context = {
            **self.admin_site.each_context(request),
            "title": _("Stock History — %(name)s") % {"name": product_name},
            "product": product,
            "product_name": product_name,
            "window_days": window_days,
            "chart_labels": labels,
            "chart_datasets": datasets,
            "recent_logs": recent_logs,
            "opts": self.model._meta,
            "change_url": reverse(
                "admin:product_product_change", args=[product.pk]
            ),
        }
        return render(request, "admin/product/stock_history.html", context)


@admin_thumbnails.thumbnail("image")
class ProductCategoryImageInline(TabularInline):
    model = ProductCategoryImage
    extra = 0
    fields = ("image_thumbnail", "image", "image_type", "active")

    tab = True
    show_change_link = True


@admin.register(ProductCategory)
class ProductCategoryAdmin(BaseTranslatableAdmin):
    ordering_field = "sort_order"
    hide_ordering_field = True
    # ``Count()`` annotations below strip the model's default
    # ``Meta.ordering`` (Django drops default ordering on GROUP BY
    # queries) — explicit ordering survives the annotation.
    ordering = ("sort_order",)
    list_display = (
        "category_info",
        "active",
        "subcategories_display",
        "image_preview",
        "created_display",
        "products_count_display",
        "recursive_products_display",
    )
    list_display_links = ("category_info",)
    search_fields = ("translations__name", "translations__description")
    list_filter = [
        "active",
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
        "parent",
    ]
    inlines = [ProductCategoryImageInline]
    readonly_fields = [
        "uuid",
        "created_at",
        "updated_at",
        "products_count_display",
        "recursive_products_display",
    ]

    fieldsets = (
        (
            _("Category Information"),
            {
                "fields": ("parent", "slug", "active"),
                "classes": ("wide",),
            },
        ),
        (
            _("Content"),
            {
                "fields": ("name", "description"),
                "classes": ("wide",),
            },
        ),
        (
            _("SEO"),
            {
                "fields": ("seo_title", "seo_description", "seo_keywords"),
                "classes": ("collapse",),
            },
        ),
        (
            _("Analytics"),
            {
                "fields": (
                    "products_count_display",
                    "recursive_products_display",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": ("uuid", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = ProductCategory.objects.add_related_count(
            qs,
            Product,
            "category",
            "products_cumulative_count",
            cumulative=True,
        )
        qs = ProductCategory.objects.add_related_count(
            qs, Product, "category", "products_count", cumulative=False
        )
        # Direct child count via annotation instead of a per-row
        # ``get_children().count()`` query.
        return qs.annotate(children_count=Count("children", distinct=True))

    def get_prepopulated_fields(self, request, obj=None):
        return {"slug": ("name",)}

    @admin.display(description=_("Category"), ordering="translations__name")
    def category_info(self, instance):
        name = instance.safe_translation_getter("name", any_language=True) or _(
            "Unnamed Category"
        )
        return f"{name} (level {instance.level})"

    @admin.display(description=_("Subcategories"), ordering="children_count")
    def subcategories_display(self, instance):
        return getattr(instance, "children_count", 0)

    @admin.display(description=_("Image"))
    def image_preview(self, instance):
        main_image = instance.main_image
        if main_image and main_image.image:
            return format_html(
                '<img src="{url}" class="h-10 w-10 rounded object-cover" />',
                url=main_image.image.url,
            )
        return "—"

    @admin.display(description=_("Created"), ordering="created_at")
    def created_display(self, instance):
        return format_dt(instance.created_at, fmt="%Y-%m-%d")

    @admin.display(description=_("Direct Products"), ordering="products_count")
    def products_count_display(self, instance):
        return getattr(instance, "products_count", 0)

    @admin.display(
        description=_("Total Products"), ordering="products_cumulative_count"
    )
    def recursive_products_display(self, instance):
        return getattr(instance, "products_cumulative_count", 0)


@admin.register(ProductReview)
class ProductReviewAdmin(BaseTranslatableAdmin):
    show_full_result_count = False  # Disable expensive COUNT(*) query

    list_display = (
        "review_info",
        "product_link",
        "user_link",
        "rating_display",
        "status_label",
        "created_display",
    )
    list_filter = [
        "status",
        ("rate", SliderNumericFilter),
        ("created_at", RangeDateTimeFilter),
        # Removed RelatedDropdownFilter for product and user - too expensive with 1.2M products
        # Use search instead to find specific products/users
    ]
    actions = [
        "approve_reviews",
        "reject_reviews",
    ]
    search_fields = [
        "user__email",
        "user__username",
        # Removed translations__comment search - too expensive with 100k+ reviews
        # Removed product__translations__name - too expensive with 1.2M products
    ]
    list_select_related = ["product", "user"]
    readonly_fields = ("created_at", "updated_at", "uuid")
    list_filter_submit = True

    def get_queryset(self, request):
        qs = super().get_queryset(request)

        # Optimize with minimal prefetching - only what's absolutely needed.
        # ``product__translations`` avoids a per-row translation query
        # from ``product_link``'s ``safe_translation_getter`` call.
        return (
            qs.select_related("product", "user")
            .prefetch_related("product__translations")
            .only(
                # Review fields - minimal set
                "id",
                "product_id",
                "user_id",
                "rate",
                "status",
                "created_at",
                # Product fields
                "product__id",
                # User fields
                "user__id",
                "user__email",
            )
        )

    fieldsets = (
        (
            _("Review Information"),
            {
                "fields": ("product", "user", "rate", "status"),
                "classes": ("wide",),
            },
        ),
        (
            _("Content"),
            {
                "fields": ("comment",),
                "classes": ("wide",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": ("uuid", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description=_("Review"))
    def review_info(self, obj):
        comment = obj.safe_translation_getter(
            "comment", any_language=True
        ) or _("No comment")
        comment_preview = (
            comment[:100] + "..." if len(comment) > 100 else comment
        )
        return f"Review #{obj.id}: {comment_preview}"

    @admin.display(description=_("Product"))
    def product_link(self, obj):
        if not obj.product:
            return "-"
        name = obj.product.safe_translation_getter(
            "name", any_language=True
        ) or str(obj.product.id)
        return format_html(
            '<a href="{url}">{name}</a>',
            url=reverse("admin:product_product_change", args=[obj.product.id]),
            name=name,
        )

    @admin.display(description=_("User"))
    def user_link(self, obj):
        if not obj.user:
            return "-"
        return format_html(
            '<a href="{url}">{email}</a>',
            url=reverse("admin:user_useraccount_change", args=[obj.user.id]),
            email=obj.user.email,
        )

    @admin.display(description=_("Rating"))
    def rating_display(self, obj):
        return f"{obj.rate}/10"

    status_label = choice_label(
        "status", variants=REVIEW_STATUS_VARIANT, description=_("Status")
    )

    @admin.display(description=_("Created"), ordering="created_at")
    def created_display(self, obj):
        return format_dt(obj.created_at)

    @action(
        description=str(_("Approve selected reviews")),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def approve_reviews(self, request, queryset):
        updated = queryset.update(status=ReviewStatus.TRUE)
        self.message_user(
            request,
            ngettext(
                "%(count)d review was approved.",
                "%(count)d reviews were approved.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=str(_("Reject selected reviews")),
        variant=ActionVariant.DANGER,
        icon="cancel",
    )
    def reject_reviews(self, request, queryset):
        updated = queryset.update(status=ReviewStatus.FALSE)
        self.message_user(
            request,
            ngettext(
                "%(count)d review was rejected.",
                "%(count)d reviews were rejected.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )


@admin.register(ProductFavourite)
class ProductFavouriteAdmin(BaseModelAdmin):
    list_display = (
        "user_display",
        "product_display",
        "created_display",
    )
    list_filter = [
        ("created_at", RangeDateTimeFilter),
        ("user", RelatedDropdownFilter),
        ("product", RelatedDropdownFilter),
        ("product__category", RelatedDropdownFilter),
    ]
    search_fields = [
        "user__email",
        "user__username",
        "product__translations__name",
        "product__sku",
    ]
    list_select_related = ["user", "product"]
    readonly_fields = ("created_at", "updated_at", "uuid")

    def get_queryset(self, request):
        # ``product_display`` calls ``safe_translation_getter``, which
        # fires one ``ProductTranslation`` query per row without
        # prefetch.
        return (
            super()
            .get_queryset(request)
            .select_related("user", "product")
            .prefetch_related("product__translations")
        )

    @admin.display(description=_("User"))
    def user_display(self, obj):
        return f"{obj.user.email} (#{obj.user.id})"

    @admin.display(description=_("Product"))
    def product_display(self, obj):
        name = obj.product.safe_translation_getter(
            "name", any_language=True
        ) or _("Unnamed Product")
        return f"{name} (#{obj.product.sku[:8]})"

    @admin.display(description=_("Created"), ordering="created_at")
    def created_display(self, obj):
        return format_dt(obj.created_at)


@admin.register(ProductCategoryImage)
@admin_thumbnails.thumbnail("image")
class ProductCategoryImageAdmin(BaseTranslatableAdmin):
    ordering_field = "sort_order"
    hide_ordering_field = True
    list_display = (
        "image_thumbnail",
        "category_name",
        "image_type_label",
        "active",
        "sort_order",
        "created_at",
    )
    list_filter = [
        "image_type",
        "active",
        ("category", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    ]
    search_fields = [
        "category__translations__name",
        "translations__title",
        "translations__alt_text",
    ]
    list_select_related = ["category"]
    readonly_fields = ("created_at", "updated_at", "uuid")
    ordering = ["category", "image_type", "sort_order"]

    fieldsets = (
        (
            _("Image Information"),
            {
                "fields": ("category", "image", "image_type", "active"),
                "classes": ("wide",),
            },
        ),
        (
            _("Content"),
            {
                "fields": ("title", "alt_text"),
                "classes": ("wide",),
            },
        ),
        (
            _("Organization"),
            {
                "fields": ("sort_order",),
                "classes": ("collapse",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": ("uuid", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        # ``category_name`` calls ``safe_translation_getter``, which
        # fires one ``ProductCategoryTranslation`` query per row
        # without prefetch.
        return (
            super()
            .get_queryset(request)
            .prefetch_related("category__translations")
        )

    @admin.display(description=_("Category"))
    def category_name(self, obj):
        return obj.category.safe_translation_getter(
            "name", any_language=True
        ) or _("Unnamed Category")

    image_type_label = choice_label(
        "image_type",
        variants=CATEGORY_IMAGE_TYPE_VARIANT,
        description=_("Type"),
    )


@admin.register(ProductImage)
@admin_thumbnails.thumbnail("image")
class ProductImageAdmin(BaseTranslatableAdmin):
    ordering_field = "sort_order"
    hide_ordering_field = True
    list_display = (
        "image_thumbnail",
        "product_name",
        "is_main",
        "sort_order",
        "created_at",
    )
    list_filter = [
        "is_main",
        ("product", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    ]
    search_fields = [
        "product__translations__name",
        "product__sku",
        "translations__title",
    ]
    list_select_related = ["product"]
    readonly_fields = ("created_at", "updated_at", "uuid")
    ordering = ["product", "-is_main", "sort_order"]

    fieldsets = (
        (
            _("Image Information"),
            {
                "fields": ("product", "image", "is_main"),
                "classes": ("wide",),
            },
        ),
        (
            _("Content"),
            {
                "fields": ("title",),
                "classes": ("wide",),
            },
        ),
        (
            _("Organization"),
            {
                "fields": ("sort_order",),
                "classes": ("collapse",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": ("uuid", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        # ``product_name`` calls ``safe_translation_getter``, which
        # fires one ``ProductTranslation`` query per row without
        # prefetch.
        return (
            super()
            .get_queryset(request)
            .prefetch_related("product__translations")
        )

    @admin.display(description=_("Product"))
    def product_name(self, obj):
        name = obj.product.safe_translation_getter(
            "name", any_language=True
        ) or _("Unnamed Product")
        return f"{name} (#{obj.product.sku[:8]})"


@admin.register(ProductVariantGroup)
class ProductVariantGroupAdmin(BaseTranslatableAdmin):
    """Admin for variant groups that link sibling product variations.

    Products are assigned to a group from the Product page (the
    ``variant_group`` autocomplete); this page is a registry of groups and an
    at-a-glance view of their members.
    """

    list_display = (
        "group_info",
        "active",
        "members_count_display",
        "created_at",
    )
    search_fields = [
        "id",
        "translations__name",
    ]
    list_filter = [
        "active",
        ("created_at", RangeDateTimeFilter),
    ]
    readonly_fields = (
        "id",
        "uuid",
        "created_at",
        "updated_at",
        "members_display",
    )
    # The Count() annotation in get_queryset strips the model's default
    # Meta.ordering (Django drops default ordering on GROUP BY queries),
    # which made the autocomplete paginator emit UnorderedObjectListWarning.
    # An explicit admin ordering survives the annotation.
    ordering = ["-created_at"]
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Variant Group"),
            {
                "fields": (
                    "name",
                    "active",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Members"),
            {
                "fields": ("members_display",),
                "description": _(
                    "Products assigned to this group. Add or remove members "
                    "from each Product's page via the Variant Group field."
                ),
            },
        ),
        (
            _("System Information"),
            {
                "fields": (
                    "id",
                    "uuid",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        # Annotated count keeps the changelist to one query; the prefetch
        # feeds ``members_display`` on the change page.
        return (
            super()
            .get_queryset(request)
            .prefetch_related("translations", "variants__translations")
            .annotate(members_count=Count("variants", distinct=True))
        )

    @admin.display(description=_("Group"), ordering="translations__name")
    def group_info(self, obj):
        name = obj.safe_translation_getter("name", any_language=True) or _(
            "Unnamed"
        )
        return f"{name} (#{obj.id})"

    @admin.display(description=_("Members"), ordering="members_count")
    def members_count_display(self, obj):
        return obj.members_count

    @admin.display(description=_("Members"))
    def members_display(self, obj):
        variants = obj.variants.all()
        if not variants:
            return _("No products assigned yet.")
        return format_html_join(
            mark_safe("<br>"),
            '<a href="{}">{} (#{})</a>',
            (
                (
                    reverse("admin:product_product_change", args=[v.pk]),
                    v.safe_translation_getter("name", any_language=True)
                    or v.sku,
                    v.pk,
                )
                for v in variants
            ),
        )


@admin.register(Brand)
class BrandAdmin(BaseModelAdmin):
    """Registry of product brands consumed by the Meta/TikTok catalog feeds.

    Products are assigned a brand from the Product page (the ``brand``
    autocomplete); this page manages the canonical brand names.
    """

    list_display = (
        "name",
        "products_count_display",
        "created_at",
    )
    search_fields = ["name"]
    list_filter = [
        ("created_at", RangeDateTimeFilter),
    ]
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Brand"),
            {
                "fields": ("name",),
                "classes": ("wide",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": (
                    "id",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(products_count=Count("products", distinct=True))
        )

    @admin.display(description=_("Products"), ordering="products_count")
    def products_count_display(self, obj):
        return obj.products_count
