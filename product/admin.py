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
from mptt.admin import DraggableMPTTAdmin
from parler.admin import TranslatableAdmin
from simple_history.admin import SimpleHistoryAdmin
from unfold.admin import TabularInline

from admin.base import BaseModelAdmin
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
    RangeNumericFilter,
    RangeNumericListFilter,
    RelatedDropdownFilter,
    SliderNumericFilter,
)
from unfold.decorators import action
from unfold.enums import ActionVariant

from core.admin import ExportModelAdmin
from core.forms.measurement import MeasurementWidget
from core.units import WeightUnits
from product.enum.review import ReviewStatus
from product.forms import ApplyDiscountForm
from product.models.attribute import Attribute
from product.models.attribute_value import AttributeValue
from product.models.category import ProductCategory
from product.models.category_image import ProductCategoryImage
from product.models.favourite import ProductFavourite
from product.models.image import ProductImage
from product.models.product import Product
from product.models.product_attribute import ProductAttribute
from product.models.review import ProductReview
from tag.admin import TaggedItemInline


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
        """Display value with translation."""
        if not obj.pk:
            return "-"
        value = (
            obj.safe_translation_getter("value", any_language=True) or "Unnamed"
        )
        return format_html(
            '<div class="text-sm">'
            '<span class="{status_class}">{status_icon}</span> '
            '<span class="font-medium text-base-900 dark:text-base-100">{value}</span>'
            "</div>",
            status_class=(
                "text-green-600 dark:text-green-400"
                if obj.active
                else "text-red-600 dark:text-red-400"
            ),
            status_icon="✓" if obj.active else "✗",
            value=value,
        )

    @admin.display(description=_("Usage"))
    def usage_count_display(self, obj):
        """Display count of products using this value."""
        if not obj.pk:
            return "-"
        count = obj.product_attributes.count()
        color_class = (
            "text-blue-600 dark:text-blue-400"
            if count > 0
            else "text-base-600 dark:text-base-300"
        )
        return format_html(
            '<span class="text-sm {color_class}">{count} products</span>',
            color_class=color_class,
            count=count,
        )


@admin.register(Attribute)
class AttributeAdmin(TranslatableAdmin, BaseModelAdmin):
    """Admin interface for managing product attributes."""

    ordering_field = "sort_order"
    hide_ordering_field = True
    list_display = [
        "attribute_info",
        "active_status_badge",
        "values_count_display",
        "usage_count_display",
        "sort_order",
        "created_display",
    ]
    search_fields = [
        "id",
        "translations__name",
    ]
    list_filter = [
        "active",
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
        "attribute_analytics",
    )
    list_select_related = []
    actions = [
        "activate_attributes",
        "deactivate_attributes",
    ]
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Attribute Information"),
            {
                "fields": (
                    "name",
                    "active",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Statistics"),
            {
                "fields": (
                    "values_count_display",
                    "usage_count_display",
                    "attribute_analytics",
                ),
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

    @admin.display(description=_("Attribute"))
    def attribute_info(self, obj):
        """Display attribute name with icon."""
        name = (
            obj.safe_translation_getter("name", any_language=True) or "Unnamed"
        )
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">📋 {name}</div>'
            '<div class="text-xs text-base-600 dark:text-base-300">ID: {id}</div>'
            "</div>",
            name=name,
            id=obj.id,
        )

    @admin.display(description=_("Status"))
    def active_status_badge(self, obj):
        """Display active status with badge."""
        if obj.active:
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                "✅ Active"
                "</span>"
            )
        return mark_safe(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
            "❌ Inactive"
            "</span>"
        )

    @admin.display(description=_("Values"))
    def values_count_display(self, obj):
        """Display count of attribute values."""
        count = getattr(obj, "values_count", 0)
        color_class = (
            "text-blue-600 dark:text-blue-400"
            if count > 0
            else "text-base-600 dark:text-base-300"
        )
        return format_html(
            '<span class="text-sm {color_class}">{count} values</span>',
            color_class=color_class,
            count=count,
        )

    @admin.display(description=_("Usage"))
    def usage_count_display(self, obj):
        """Display count of products using this attribute."""
        count = getattr(obj, "usage_count", 0)

        if count > 10:
            color_class = "text-green-600 dark:text-green-400"
        elif count > 0:
            color_class = "text-blue-600 dark:text-blue-400"
        else:
            color_class = "text-base-600 dark:text-base-300"

        return format_html(
            '<span class="text-sm {color_class}">{count} products</span>',
            color_class=color_class,
            count=count,
        )

    @admin.display(description=_("Created"))
    def created_display(self, obj):
        """Display creation date with relative time."""
        now = timezone.now()
        diff = now - obj.created_at

        if diff < timedelta(days=1):
            time_ago = "Today"
            color = "text-green-600 dark:text-green-400"
        elif diff < timedelta(days=7):
            time_ago = f"{diff.days}d ago"
            color = "text-blue-600 dark:text-blue-400"
        else:
            time_ago = obj.created_at.strftime("%Y-%m-%d")
            color = "text-base-600 dark:text-base-400"

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{date}</div>'
            '<div class="{color}">{time_ago}</div>'
            "</div>",
            date=obj.created_at.strftime("%Y-%m-%d"),
            color=color,
            time_ago=time_ago,
        )

    @admin.display(description=_("Analytics"))
    def attribute_analytics(self, obj):
        """Display detailed analytics for the attribute."""
        if not obj.created_at:
            return "Available after creation."

        now = timezone.now()
        age = now - obj.created_at
        last_updated = now - obj.updated_at

        values_count = getattr(obj, "values_count", 0)
        usage_count = getattr(obj, "usage_count", 0)
        active_values = obj.values.filter(active=True).count()
        inactive_values = values_count - active_values

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Attribute Age:</strong></div><div>{age} days</div>"
            "<div><strong>Last Updated:</strong></div><div>{updated} days ago</div>"
            "<div><strong>Total Values:</strong></div><div>{values}</div>"
            "<div><strong>Active Values:</strong></div><div>{active_values}</div>"
            "<div><strong>Inactive Values:</strong></div><div>{inactive_values}</div>"
            "<div><strong>Products Using:</strong></div><div>{usage}</div>"
            "</div>"
            "</div>",
            age=age.days,
            updated=last_updated.days,
            values=values_count,
            active_values=active_values,
            inactive_values=inactive_values,
            usage=usage_count,
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
class AttributeValueAdmin(TranslatableAdmin, BaseModelAdmin):
    """Admin interface for managing attribute values."""

    ordering_field = "sort_order"
    hide_ordering_field = True
    list_display = [
        "value_info",
        "attribute_display",
        "active_status_badge",
        "usage_count_display",
        "sort_order",
        "created_display",
    ]
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
        "value_analytics",
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
                "fields": (
                    "attribute",
                    "value",
                    "active",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Statistics"),
            {
                "fields": (
                    "usage_count_display",
                    "value_analytics",
                ),
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

    @admin.display(description=_("Value"))
    def value_info(self, obj):
        """Display value with icon."""
        value = (
            obj.safe_translation_getter("value", any_language=True) or "Unnamed"
        )
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">🏷️ {value}</div>'
            '<div class="text-xs text-base-600 dark:text-base-300">ID: {id}</div>'
            "</div>",
            value=value,
            id=obj.id,
        )

    @admin.display(description=_("Attribute"))
    def attribute_display(self, obj):
        """Display parent attribute name."""
        if obj.attribute:
            attribute_name = (
                obj.attribute.safe_translation_getter("name", any_language=True)
                or "Unnamed"
            )
            return format_html(
                '<span class="text-sm text-base-900 dark:text-base-100">{name}</span>',
                name=attribute_name,
            )
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300">No Attribute</span>'
        )

    @admin.display(description=_("Status"))
    def active_status_badge(self, obj):
        """Display active status with badge."""
        if obj.active:
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                "✅ Active"
                "</span>"
            )
        return mark_safe(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
            "❌ Inactive"
            "</span>"
        )

    @admin.display(description=_("Usage"))
    def usage_count_display(self, obj):
        """Display count of products using this value."""
        count = getattr(obj, "usage_count", 0)

        if count > 10:
            color_class = "text-green-600 dark:text-green-400"
        elif count > 0:
            color_class = "text-blue-600 dark:text-blue-400"
        else:
            color_class = "text-base-600 dark:text-base-300"

        return format_html(
            '<span class="text-sm {color_class}">{count} products</span>',
            color_class=color_class,
            count=count,
        )

    @admin.display(description=_("Created"))
    def created_display(self, obj):
        """Display creation date with relative time."""
        now = timezone.now()
        diff = now - obj.created_at

        if diff < timedelta(days=1):
            time_ago = "Today"
            color = "text-green-600 dark:text-green-400"
        elif diff < timedelta(days=7):
            time_ago = f"{diff.days}d ago"
            color = "text-blue-600 dark:text-blue-400"
        else:
            time_ago = obj.created_at.strftime("%Y-%m-%d")
            color = "text-base-600 dark:text-base-400"

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{date}</div>'
            '<div class="{color}">{time_ago}</div>'
            "</div>",
            date=obj.created_at.strftime("%Y-%m-%d"),
            color=color,
            time_ago=time_ago,
        )

    @admin.display(description=_("Analytics"))
    def value_analytics(self, obj):
        """Display detailed analytics for the value."""
        if not obj.created_at:
            return "Available after creation."

        now = timezone.now()
        age = now - obj.created_at
        last_updated = now - obj.updated_at

        usage_count = getattr(obj, "usage_count", 0)
        attribute_name = (
            obj.attribute.safe_translation_getter("name", any_language=True)
            if obj.attribute
            else "N/A"
        )

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Value Age:</strong></div><div>{age} days</div>"
            "<div><strong>Last Updated:</strong></div><div>{updated} days ago</div>"
            "<div><strong>Parent Attribute:</strong></div><div>{attribute}</div>"
            "<div><strong>Products Using:</strong></div><div>{usage}</div>"
            "</div>"
            "</div>",
            age=age.days,
            updated=last_updated.days,
            attribute=attribute_name,
            usage=usage_count,
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
    fields = ("image_preview", "image", "is_main")
    readonly_fields = ("image_preview",)

    tab = True
    show_change_link = True

    @admin.display(description=_("Preview"))
    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{url}" '
                'style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb;" />',
                url=obj.image.url,
            )
        return mark_safe(
            '<div class="bg-gray-100 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-700 flex items-center justify-center text-base-600 dark:text-base-300 text-xs">No Image</div>'
        )


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
        "status_badge",
    )
    readonly_fields = (
        "reservation_info",
        "quantity",
        "session_info",
        "expires_display",
        "status_badge",
    )

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
        """Display reservation ID and creation time."""
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">#{id}</div>'
            '<div class="text-xs text-base-600 dark:text-base-300">{created}</div>'
            "</div>",
            id=obj.id,
            created=obj.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    @admin.display(description=_("Reserved By"))
    def session_info(self, obj):
        """Display user or session information."""
        if obj.reserved_by:
            user_display = obj.reserved_by.email or obj.reserved_by.username
            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-base-900 dark:text-base-100">👤 {user}</div>'
                "</div>",
                user=user_display,
            )
        session_short = (
            obj.session_id[:8] if len(obj.session_id) > 8 else obj.session_id
        )
        return format_html(
            '<div class="text-sm">'
            '<div class="text-base-600 dark:text-base-400">🔒 Guest: {session}...</div>'
            "</div>",
            session=session_short,
        )

    @admin.display(description=_("Expires In"))
    def expires_display(self, obj):
        """Display expiration time with countdown."""
        now = timezone.now()
        time_left = obj.expires_at - now
        minutes_left = int(time_left.total_seconds() / 60)

        if minutes_left <= 2:
            color_class = "text-red-600 dark:text-red-400"
            icon = "🔴"
        elif minutes_left <= 5:
            color_class = "text-orange-600 dark:text-orange-400"
            icon = "🟠"
        else:
            color_class = "text-green-600 dark:text-green-400"
            icon = "🟢"

        return format_html(
            '<div class="text-sm {color_class}">'
            "{icon} {minutes} min left"
            "</div>",
            color_class=color_class,
            icon=icon,
            minutes=minutes_left,
        )

    @admin.display(description=_("Status"))
    def status_badge(self, obj):
        """Display reservation status badge."""
        if obj.order:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">'
                "📦 Order #{order_id}"
                "</span>",
                order_id=obj.order.id,
            )
        return mark_safe(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            'bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded-full">'
            "⏳ Pending Checkout"
            "</span>"
        )


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
    readonly_fields = (
        "operation_display",
        "quantity_change",
        "stock_levels",
        "order_link",
        "performed_by_display",
        "timestamp_display",
    )

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
        """Display operation type with icon."""
        operation_icons = {
            "RESERVE": "🔒",
            "RELEASE": "🔓",
            "DECREMENT": "📉",
            "INCREMENT": "📈",
        }
        color_map = {
            "RESERVE": "text-blue-600 dark:text-blue-400",
            "RELEASE": "text-green-600 dark:text-green-400",
            "DECREMENT": "text-red-600 dark:text-red-400",
            "INCREMENT": "text-green-600 dark:text-green-400",
        }
        return format_html(
            '<div class="text-sm {color_class} font-medium">'
            "{icon} {label}"
            "</div>",
            color_class=color_map.get(
                obj.operation_type, "text-base-600 dark:text-base-400"
            ),
            icon=operation_icons.get(obj.operation_type, "📝"),
            label=obj.get_operation_type_display(),
        )

    @admin.display(description=_("Change"))
    def quantity_change(self, obj):
        """Display quantity delta with +/- indicator."""
        delta = obj.quantity_delta

        if delta > 0:
            return format_html(
                '<span class="text-sm font-medium text-green-600 dark:text-green-400">'
                "+{delta}"
                "</span>",
                delta=delta,
            )
        if delta < 0:
            return format_html(
                '<span class="text-sm font-medium text-red-600 dark:text-red-400">'
                "-{delta}"
                "</span>",
                delta=abs(delta),
            )
        return mark_safe(
            '<span class="text-sm text-base-600 dark:text-base-300">0</span>'
        )

    @admin.display(description=_("Stock Level"))
    def stock_levels(self, obj):
        """Display before/after stock levels."""
        return format_html(
            '<div class="text-sm text-base-600 dark:text-base-400">'
            "{before} → {after}"
            "</div>",
            before=obj.stock_before,
            after=obj.stock_after,
        )

    @admin.display(description=_("Related Order"))
    def order_link(self, obj):
        """Display linked order if any."""
        if obj.order:
            return format_html(
                '<a href="{url}" class="text-sm text-blue-600 dark:text-blue-400 hover:underline">'
                "Order #{order_id}"
                "</a>",
                url=reverse_lazy(
                    "admin:order_order_change", args=[obj.order.id]
                ),
                order_id=obj.order.id,
            )

        reason = (obj.reason or "N/A")[:45]
        return format_html(
            '<span class="text-sm text-base-600 dark:text-base-300">{reason}</span>',
            reason=reason,
        )

    @admin.display(description=_("By"))
    def performed_by_display(self, obj):
        """Display who performed the operation."""
        if obj.performed_by:
            user_display = (
                obj.performed_by.email or obj.performed_by.username
            )[:20]
            return format_html(
                '<span class="text-sm text-base-600 dark:text-base-400">{user}</span>',
                user=user_display,
            )
        return mark_safe(
            '<span class="text-sm text-base-600 dark:text-base-300">System</span>'
        )

    @admin.display(description=_("Time"))
    def timestamp_display(self, obj):
        """Display operation timestamp."""
        return format_html(
            '<span class="text-sm text-base-600 dark:text-base-300">{ts}</span>',
            ts=obj.created_at.strftime("%m/%d %H:%M"),
        )


@admin.register(Product)
class ProductAdmin(
    TranslatableAdmin, ExportModelAdmin, SimpleHistoryAdmin, BaseModelAdmin
):
    list_display = [
        "product_info",
        "category_display",
        "pricing_info",
        "stock_info",
        "performance_metrics",
        "status_badges",
        "created_display",
    ]
    search_fields = [
        "id",
        "sku",
        "translations__name",
        "translations__description",
        "category__translations__name",
    ]
    list_filter = [
        StockStatusFilter,
        StockReservationStatusFilter,
        PriceRangeFilter,
        DiscountStatusFilter,
        PopularityFilter,
        "active",
        ("category", RelatedDropdownFilter),
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
        "product_analytics",
        "pricing_summary",
        "performance_summary",
        "stock_reservation_summary",
    )
    list_select_related = ["category", "vat", "changed_by"]
    autocomplete_fields = ["category", "vat"]
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
                    "active",
                    "name",
                    "description",
                ),
                "classes": ("tab",),
            },
        ),
        (
            _("Pricing & Inventory"),
            {
                "fields": (
                    "price",
                    "discount_percent",
                    "vat",
                    "pricing_summary",
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
                "fields": (
                    "view_count",
                    "likes_count",
                    "performance_summary",
                    "product_analytics",
                ),
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
        # the cost from "2.4s once" to "~1ms × 25 rows = ~25ms".
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

        return (
            qs.select_related("category", "vat", "changed_by")
            .prefetch_related(
                main_images,
                active_reservations,
                Prefetch(
                    "category__parent", queryset=ProductCategory.objects.all()
                ),
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
            )
        )

    def get_prepopulated_fields(self, request, obj=None):
        return {"slug": ("name",)}

    @admin.display(description=_("Product"))
    def product_info(self, obj):
        name = (
            obj.safe_translation_getter("name", any_language=True)
            or "Untitled Product"
        )
        main_images = getattr(obj, "main_images_list", [])
        main_image = main_images[0] if main_images else None

        if main_image and main_image.image:
            image_html = format_html(
                '<img src="{url}" '
                'style="width: 50px; height: 50px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb; margin-right: 12px;" />',
                url=main_image.image.url,
            )
        else:
            image_html = mark_safe(
                '<div style="width: 50px; height: 50px; background: #f3f4f6; border-radius: 8px; border: 1px solid #e5e7eb; margin-right: 12px; display: flex; align-items: center; justify-content: center; color: #9ca3af; font-size: 20px;">📦</div>'
            )

        return format_html(
            '<div style="display: flex; align-items: center;">'
            "{image_html}"
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{name}</div>'
            '<div class="text-base-600 dark:text-base-300">#{sku}</div>'
            "</div>"
            "</div>",
            image_html=image_html,
            name=name,
            sku=obj.sku[:8],
        )

    @admin.display(description=_("Category"))
    def category_display(self, obj):
        if obj.category:
            category_name = (
                obj.category.safe_translation_getter("name", any_language=True)
                or "Unnamed"
            )
            category_path = " → ".join(
                [
                    cat.safe_translation_getter("name", any_language=True)
                    or "Unnamed"
                    for cat in obj.category.get_ancestors()
                ]
            )

            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-base-900 dark:text-base-100">{name}</div>'
                '<div class="text-xs text-base-600 dark:text-base-300">{path}</div>'
                "</div>",
                name=category_name,
                path=category_path or "Root",
            )
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300">No Category</span>'
        )

    @admin.display(description=_("Pricing"))
    def pricing_info(self, obj):
        price = obj.price
        final_price = obj.final_price
        discount = obj.discount_percent

        price_class = (
            "text-base-600 dark:text-base-300 line-through"
            if discount > 0
            else "font-bold text-base-900 dark:text-base-100"
        )

        if discount > 0:
            return format_html(
                '<div class="text-sm">'
                '<div class="{price_class}">{price}</div>'
                '<div class="font-bold text-green-600 dark:text-green-400">{final}</div>'
                '<div class="text-xs text-red-600 dark:text-red-400">-{discount}%</div>'
                "</div>",
                price_class=price_class,
                price=str(price),
                final=str(final_price),
                discount=discount,
            )
        return format_html(
            '<div class="text-sm">'
            '<div class="{price_class}">{price}</div>'
            "</div>",
            price_class=price_class,
            price=str(price),
        )

    @admin.display(description=_("Stock"))
    def stock_info(self, obj):
        stock = obj.stock

        reserved_qty = sum(
            reservation.quantity
            for reservation in getattr(obj, "active_reservations_list", [])
        )
        available_stock = stock - reserved_qty

        if stock == 0:
            stock_badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "❌ Out of Stock"
                "</span>"
            )
        elif stock <= 5:
            stock_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                "⚠️ Critical ({stock})"
                "</span>",
                stock=stock,
            )
        elif stock <= 10:
            stock_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded-full">'
                "🔶 Low ({stock})"
                "</span>",
                stock=stock,
            )
        else:
            stock_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                "✅ In Stock ({stock})"
                "</span>",
                stock=stock,
            )

        if reserved_qty > 0:
            reservation_info = format_html(
                '<div class="text-xs text-blue-600 dark:text-blue-400 mt-1">'
                "🔒 {reserved} reserved"
                "</div>"
                '<div class="text-xs text-green-600 dark:text-green-400">'
                "✓ {available} available"
                "</div>",
                reserved=reserved_qty,
                available=available_stock,
            )
        else:
            reservation_info = ""

        return format_html(
            '<div class="text-sm">{badge}{info}</div>',
            badge=stock_badge,
            info=reservation_info,
        )

    @admin.display(description=_("Performance"))
    def performance_metrics(self, obj):
        views = obj.view_count
        likes = obj.likes_count
        rating = obj.review_average
        rating_formatted = "{:.1f}".format(float(rating))

        return format_html(
            '<div class="text-sm">'
            '<div class="text-base-600 dark:text-base-400">👁️ {views}</div>'
            '<div class="text-base-600 dark:text-base-400">❤️ {likes}</div>'
            '<div class="text-base-600 dark:text-base-400">⭐ {rating}/10</div>'
            "</div>",
            views=views,
            likes=likes,
            rating=rating_formatted,
        )

    @admin.display(description=_("Status"))
    def status_badges(self, obj):
        badges = []

        if obj.active:
            badges.append(
                mark_safe(
                    '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full border border-green-200 dark:border-green-700" title="Product is active and visible to customers">'
                    "✅ Active"
                    "</span>"
                )
            )
        else:
            badges.append(
                mark_safe(
                    '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full border border-red-200 dark:border-red-700" title="Product is inactive and hidden from customers">'
                    "❌ Inactive"
                    "</span>"
                )
            )

        if obj.stock <= 0:
            badges.append(
                mark_safe(
                    '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-gray-50 dark:bg-gray-900 text-base-700 dark:text-base-300 rounded-full border border-gray-200 dark:border-gray-700" title="Product is out of stock">'
                    "📦 Out of Stock"
                    "</span>"
                )
            )
        elif obj.stock <= 10:
            badges.append(
                format_html(
                    '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded-full border border-yellow-200 dark:border-yellow-700" title="Low stock - only {stock} units remaining">'
                    "⚠️ Low Stock"
                    "</span>",
                    stock=obj.stock,
                )
            )

        if obj.discount_percent > 0:
            badges.append(
                format_html(
                    '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full border border-orange-200 dark:border-orange-700" title="Product has {discount}% discount applied">'
                    "🏷️ {discount}% OFF"
                    "</span>",
                    discount=obj.discount_percent,
                )
            )

        thirty_days_ago = timezone.now() - timedelta(days=30)
        if obj.created_at >= thirty_days_ago:
            days_old = (timezone.now() - obj.created_at).days
            badges.append(
                format_html(
                    '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full border border-blue-200 dark:border-blue-700" title="Product added {days} days ago">'
                    "🆕 New"
                    "</span>",
                    days=days_old,
                )
            )

        if hasattr(obj, "is_featured") and obj.is_featured:
            badges.append(
                mark_safe(
                    '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-300 rounded-full border border-purple-200 dark:border-purple-700" title="This product is featured on the homepage">'
                    "⭐ Featured"
                    "</span>"
                )
            )

        views = obj.view_count
        if views > 100:
            badges.append(
                format_html(
                    '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-indigo-50 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-300 rounded-full border border-indigo-200 dark:border-indigo-700" title="Product has {views} views - performing well">'
                    "🔥 Popular"
                    "</span>",
                    views=views,
                )
            )

        return format_html(
            '<div class="flex flex-wrap gap-1">{badges}</div>',
            badges=format_html_join("", "{}", ((b,) for b in badges)),
        )

    @admin.display(description=_("Created"))
    def created_display(self, obj):
        now = timezone.now()
        diff = now - obj.created_at

        if diff < timedelta(days=1):
            time_ago = "Today"
            color = "text-green-600 dark:text-green-400"
        elif diff < timedelta(days=7):
            time_ago = f"{diff.days}d ago"
            color = "text-blue-600 dark:text-blue-400"
        else:
            time_ago = obj.created_at.strftime("%Y-%m-%d")
            color = "text-base-600 dark:text-base-400"

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{date}</div>'
            '<div class="{color}">{time_ago}</div>'
            "</div>",
            date=obj.created_at.strftime("%Y-%m-%d"),
            color=color,
            time_ago=time_ago,
        )

    @admin.display(description=_("Pricing Summary"))
    def pricing_summary(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Base Price:</strong></div><div>{price}</div>"
            "<div><strong>Discount %:</strong></div><div>{discount}%</div>"
            "<div><strong>Discount Value:</strong></div><div>{discount_val}</div>"
            "<div><strong>VAT %:</strong></div><div>{vat_pct}%</div>"
            "<div><strong>VAT Value:</strong></div><div>{vat_val}</div>"
            '<div><strong>Final Price:</strong></div><div class="font-bold">{final}</div>'
            "</div>"
            "</div>",
            price=str(obj.price),
            discount=obj.discount_percent,
            discount_val=str(obj.discount_value),
            vat_pct=obj.vat_percent,
            vat_val=str(obj.vat_value),
            final=str(obj.final_price),
        )

    @admin.display(description=_("Performance Summary"))
    def performance_summary(self, obj):
        views = obj.view_count
        likes = obj.likes_count
        rating = obj.review_average
        review_count = obj.review_count
        favorites_count = obj.favourited_by.count()

        rating_formatted = "{:.1f}".format(float(rating))
        engagement_formatted = "%.1f" % (
            ((likes + favorites_count) / max(views, 1)) * 100
        )

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Total Views:</strong></div><div>{views}</div>"
            "<div><strong>Likes:</strong></div><div>{likes}</div>"
            "<div><strong>Favorites:</strong></div><div>{favorites}</div>"
            "<div><strong>Reviews:</strong></div><div>{reviews}</div>"
            "<div><strong>Avg Rating:</strong></div><div>{rating}/10</div>"
            "<div><strong>Engagement:</strong></div><div>{engagement}%</div>"
            "</div>"
            "</div>",
            views=views,
            likes=likes,
            favorites=favorites_count,
            reviews=review_count,
            rating=rating_formatted,
            engagement=engagement_formatted,
        )

    @admin.display(description=_("Stock Reservation Summary"))
    def stock_reservation_summary(self, obj):
        """Display stock reservation statistics and recent activity."""
        from order.models.stock_reservation import StockReservation
        from order.models.stock_log import StockLog

        now = timezone.now()

        # Get active reservations count and total quantity
        active_reservations = StockReservation.objects.filter(
            product=obj, expires_at__gt=now, consumed=False
        )

        reservation_count = active_reservations.count()
        reserved_qty = (
            active_reservations.aggregate(total=Sum("quantity"))["total"] or 0
        )
        available_stock = obj.stock - reserved_qty

        # Get recent stock operations count (last 7 days)
        seven_days_ago = now - timedelta(days=7)
        recent_operations = StockLog.objects.filter(
            product=obj, created_at__gte=seven_days_ago
        )

        operations_count = recent_operations.count()
        reserve_count = recent_operations.filter(
            operation_type="RESERVE"
        ).count()
        release_count = recent_operations.filter(
            operation_type="RELEASE"
        ).count()
        decrement_count = recent_operations.filter(
            operation_type="DECREMENT"
        ).count()
        increment_count = recent_operations.filter(
            operation_type="INCREMENT"
        ).count()

        # Calculate reservation percentage
        reservation_pct = (
            (reserved_qty / max(obj.stock, 1)) * 100 if obj.stock > 0 else 0
        )

        if reservation_pct > 50:
            pct_color = "text-red-600 dark:text-red-400"
        elif reservation_pct > 25:
            pct_color = "text-orange-600 dark:text-orange-400"
        else:
            pct_color = "text-green-600 dark:text-green-400"

        history_url = reverse(
            "admin:product_product_stock_history", args=[obj.pk]
        )

        return format_html(
            '<div class="text-sm">'
            '<div class="mb-3">'
            '<h4 class="font-semibold text-base-900 dark:text-base-100 mb-2">Current Stock Status</h4>'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Total Stock:</strong></div><div>{stock}</div>"
            '<div><strong>Reserved:</strong></div><div class="text-blue-600 dark:text-blue-400">{reserved}</div>'
            '<div><strong>Available:</strong></div><div class="text-green-600 dark:text-green-400">{available}</div>'
            "<div><strong>Active Reservations:</strong></div><div>{res_count}</div>"
            '<div><strong>Reserved %:</strong></div><div class="{pct_color} font-medium">{res_pct}%</div>'
            "</div>"
            "</div>"
            "<div>"
            '<h4 class="font-semibold text-base-900 dark:text-base-100 mb-2">Recent Activity (7 days)</h4>'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Total Operations:</strong></div><div>{ops_count}</div>"
            "<div><strong>🔒 Reserves:</strong></div><div>{reserve}</div>"
            "<div><strong>🔓 Releases:</strong></div><div>{release}</div>"
            "<div><strong>📉 Decrements:</strong></div><div>{decrement}</div>"
            "<div><strong>📈 Increments:</strong></div><div>{increment}</div>"
            "</div>"
            "</div>"
            '<div class="mt-3"><a href="{history_url}" '
            'class="inline-flex items-center gap-1 rounded-md border border-gray-300 '
            "bg-white px-3 py-1.5 text-xs font-medium text-gray-700 "
            "hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-800 "
            'dark:text-gray-200 dark:hover:bg-gray-700">{history_label}</a></div>'
            "</div>",
            stock=obj.stock,
            reserved=reserved_qty,
            available=available_stock,
            res_count=reservation_count,
            pct_color=pct_color,
            res_pct=f"{reservation_pct:.1f}",
            ops_count=operations_count,
            reserve=reserve_count,
            release=release_count,
            decrement=decrement_count,
            increment=increment_count,
            history_url=history_url,
            history_label=str(_("Open full stock history chart →")),
        )

    @admin.display(description=_("Product Analytics"))
    def product_analytics(self, obj):
        if not obj.created_at:
            return "Available after creation."

        now = timezone.now()
        age = now - obj.created_at
        last_updated = now - obj.updated_at

        views = obj.view_count
        likes = obj.likes_count
        engagement_rate = (likes / max(views, 1)) * 100
        engagement_formatted = "{:.1f}".format(engagement_rate)

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Product Age:</strong></div><div>{age}d</div>"
            "<div><strong>Last Updated:</strong></div><div>{updated}d ago</div>"
            "<div><strong>Stock Value:</strong></div><div>{stock_value}</div>"
            "<div><strong>Engagement Rate:</strong></div><div>{engagement}%</div>"
            "<div><strong>Has Images:</strong></div><div>{has_images}</div>"
            "<div><strong>Has Reviews:</strong></div><div>{has_reviews}</div>"
            "<div><strong>In Category:</strong></div><div>{in_category}</div>"
            "<div><strong>Changed By:</strong></div><div>{changed_by}</div>"
            "</div>"
            "</div>",
            age=age.days,
            updated=last_updated.days,
            stock_value=str(obj.final_price.amount * obj.stock),
            engagement=engagement_formatted,
            has_images="Yes" if obj.images.exists() else "No",
            has_reviews="Yes" if obj.reviews.exists() else "No",
            in_category="Yes" if obj.category else "No",
            changed_by=obj.changed_by.email if obj.changed_by else "System",
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
                "Cloned product #%(orig)s → draft #%(clone)s. Edit the copy "
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
    fields = ("image_preview", "image", "image_type", "active")
    readonly_fields = ("image_preview",)

    tab = True
    show_change_link = True

    @admin.display(description=_("Preview"))
    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{url}" '
                'style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb;" />',
                url=obj.image.url,
            )
        return mark_safe(
            '<div class="bg-gray-100 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-700 flex items-center justify-center text-base-600 dark:text-base-300 text-xs">No Image</div>'
        )


@admin.register(ProductCategory)
class ProductCategoryAdmin(
    BaseModelAdmin, TranslatableAdmin, DraggableMPTTAdmin
):
    mptt_indent_field = "translations__name"
    list_display = (
        "tree_actions",
        "indented_title",
        "category_info",
        "category_stats",
        "category_status",
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
        "category_analytics",
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
                    "category_analytics",
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
        return qs

    def get_prepopulated_fields(self, request, obj=None):
        return {"slug": ("name",)}

    @admin.display(description=_("Category"))
    def category_info(self, instance):
        name = (
            instance.safe_translation_getter("name", any_language=True)
            or "Unnamed Category"
        )
        path = " → ".join(
            [
                cat.safe_translation_getter("name", any_language=True)
                or "Unnamed"
                for cat in instance.get_ancestors()
            ]
        )

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{name}</div>'
            '<div class="text-base-600 dark:text-base-300">Level: {level}</div>'
            '<div class="text-xs text-base-600 dark:text-base-300">{path}</div>'
            "</div>",
            name=name,
            level=instance.level,
            path=path or "Root Category",
        )

    @admin.display(description=_("Product Stats"))
    def category_stats(self, instance):
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">Direct: {direct}</div>'
            '<div class="text-base-700 dark:text-base-200">Total: {total}</div>'
            '<div class="text-base-600 dark:text-base-300">Subcategories: {children}</div>'
            "</div>",
            direct=getattr(instance, "products_count", 0),
            total=getattr(instance, "products_cumulative_count", 0),
            children=instance.get_children().count(),
        )

    @admin.display(description=_("Status"))
    def category_status(self, instance):
        if instance.active:
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                "✅ Active"
                "</span>"
            )
        return mark_safe(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
            "❌ Inactive"
            "</span>"
        )

    @admin.display(description=_("Image"))
    def image_preview(self, instance):
        main_image = instance.main_image
        if main_image and main_image.image:
            return format_html(
                '<img src="{url}" '
                'style="width: 40px; height: 40px; object-fit: cover; border-radius: 6px; border: 1px solid #e5e7eb;" />',
                url=main_image.image.url,
            )
        return mark_safe(
            '<div class="bg-gray-100 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-700 flex items-center justify-center text-base-600 dark:text-base-300 text-xs">'
            "🖼"
            "</div>"
        )

    @admin.display(description=_("Created"))
    def created_display(self, instance):
        return format_html(
            '<div class="text-sm text-base-600 dark:text-base-400">{date}</div>',
            date=instance.created_at.strftime("%Y-%m-%d"),
        )

    @admin.display(description=_("Category Analytics"))
    def category_analytics(self, instance):
        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Level:</strong></div><div>{level}</div>"
            "<div><strong>Ancestors:</strong></div><div>{ancestors}</div>"
            "<div><strong>Descendants:</strong></div><div>{descendants}</div>"
            "<div><strong>Siblings:</strong></div><div>{siblings}</div>"
            "<div><strong>Direct Products:</strong></div><div>{direct}</div>"
            "<div><strong>Total Products:</strong></div><div>{total}</div>"
            "</div>"
            "</div>",
            level=instance.level,
            ancestors=instance.get_ancestors().count(),
            descendants=instance.get_descendants().count(),
            siblings=instance.get_siblings().count(),
            direct=getattr(instance, "products_count", 0),
            total=getattr(instance, "products_cumulative_count", 0),
        )

    @admin.display(description=_("Direct Products"))
    def products_count_display(self, instance):
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-semibold '
            'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">'
            "{count}"
            "</span>",
            count=getattr(instance, "products_count", 0),
        )

    @admin.display(description=_("Total Products"))
    def recursive_products_display(self, instance):
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            'bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-300 rounded-full">'
            "{count}"
            "</span>",
            count=getattr(instance, "products_cumulative_count", 0),
        )


@admin.register(ProductReview)
class ProductReviewAdmin(BaseModelAdmin, TranslatableAdmin):
    show_full_result_count = False  # Disable expensive COUNT(*) query

    list_display = [
        "review_info",
        "product_link",
        "user_link",
        "rating_display",
        "status_badge",
        "created_display",
    ]
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
        "make_published",
        "make_unpublished",
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

        # Optimize with minimal prefetching - only what's absolutely needed
        # Avoid loading all translations upfront
        return qs.select_related("product", "user").only(
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
        comment = (
            obj.safe_translation_getter("comment", any_language=True)
            or "No comment"
        )
        comment_preview = (
            comment[:100] + "..." if len(comment) > 100 else comment
        )

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">Review #{id}</div>'
            '<div class="text-base-600 dark:text-base-400">{comment}</div>'
            "</div>",
            id=obj.id,
            comment=comment_preview,
        )

    @admin.display(description=_("Product"))
    def product_link(self, obj):
        if obj.product:
            name = obj.product.safe_translation_getter(
                "name", any_language=True
            ) or str(obj.product.id)
            return format_html(
                '<div class="text-sm">'
                '<a href="{url}" class="font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300">{name}</a>'
                '<div class="text-base-600 dark:text-base-300">#{id}</div>'
                "</div>",
                url=f"/admin/product/product/{obj.product.id}/change/",
                name=name,
                id=obj.product.id,
            )
        return "-"

    @admin.display(description=_("User"))
    def user_link(self, obj):
        if obj.user:
            return format_html(
                '<div class="text-sm">'
                '<a href="{url}" class="font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300">{email}</a>'
                '<div class="text-base-600 dark:text-base-300">ID: {id}</div>'
                "</div>",
                url=f"/admin/user/useraccount/{obj.user.id}/change/",
                email=obj.user.email,
                id=obj.user.id,
            )
        return "-"

    @admin.display(description=_("Rating"))
    def rating_display(self, obj):
        rate = obj.rate
        stars = "⭐" * rate + "☆" * (10 - rate)

        if rate >= 8:
            color = "text-green-600 dark:text-green-400"
        elif rate >= 6:
            color = "text-yellow-600 dark:text-yellow-400"
        else:
            color = "text-red-600 dark:text-red-400"

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium {color}">{rate}/10</div>'
            '<div class="text-xs">{stars}</div>'
            "</div>",
            color=color,
            rate=rate,
            stars=stars[:5],
        )

    @admin.display(description=_("Status"))
    def status_badge(self, obj):
        status_config = {
            ReviewStatus.NEW: {
                "bg": "bg-blue-50 dark:bg-blue-900",
                "text": "text-blue-700 dark:text-blue-300",
                "icon": "🆕",
            },
            ReviewStatus.TRUE: {
                "bg": "bg-green-50 dark:bg-green-900",
                "text": "text-green-700 dark:text-green-300",
                "icon": "✅",
            },
            ReviewStatus.FALSE: {
                "bg": "bg-red-50 dark:bg-red-900",
                "text": "text-red-700 dark:text-red-300",
                "icon": "❌",
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
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            '{bg} {text_class} rounded-full gap-1">'
            "<span>{icon}</span>"
            "<span>{label}</span>"
            "</span>",
            bg=config["bg"],
            text_class=config["text"],
            icon=config["icon"],
            label=obj.get_status_display(),
        )

    @admin.display(description=_("Created"))
    def created_display(self, obj):
        return format_html(
            '<div class="text-sm text-base-600 dark:text-base-400">{date}</div>',
            date=obj.created_at.strftime("%Y-%m-%d %H:%M"),
        )

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

    def make_published(self, request, queryset):
        updated = queryset.update(status=ReviewStatus.TRUE)
        self.message_user(
            request,
            ngettext(
                "%(count)d comment was successfully marked as published.",
                "%(count)d comments were successfully marked as published.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    def make_unpublished(self, request, queryset):
        updated = queryset.update(status=ReviewStatus.FALSE)
        self.message_user(
            request,
            ngettext(
                "%(count)d comment was successfully marked as unpublished.",
                "%(count)d comments were successfully marked as unpublished.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )


@admin.register(ProductFavourite)
class ProductFavouriteAdmin(BaseModelAdmin):
    list_display = [
        "user_display",
        "product_display",
        "created_display",
    ]
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

    @admin.display(description=_("User"))
    def user_display(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{email}</div>'
            '<div class="text-base-600 dark:text-base-300">ID: {id}</div>'
            "</div>",
            email=obj.user.email,
            id=obj.user.id,
        )

    @admin.display(description=_("Product"))
    def product_display(self, obj):
        name = (
            obj.product.safe_translation_getter("name", any_language=True)
            or "Unnamed Product"
        )
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{name}</div>'
            '<div class="text-base-600 dark:text-base-300">#{sku}</div>'
            "</div>",
            name=name,
            sku=obj.product.sku[:8],
        )

    @admin.display(description=_("Created"))
    def created_display(self, obj):
        return format_html(
            '<div class="text-sm text-base-600 dark:text-base-400">{date}</div>',
            date=obj.created_at.strftime("%Y-%m-%d %H:%M"),
        )


@admin.register(ProductCategoryImage)
class ProductCategoryImageAdmin(BaseModelAdmin, TranslatableAdmin):
    ordering_field = "sort_order"
    hide_ordering_field = True
    list_display = [
        "image_preview",
        "category_name",
        "image_type_badge",
        "status_badge",
        "sort_order",
        "created_at",
    ]
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

    @admin.display(description=_("Preview"))
    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{url}" '
                'style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb;" />',
                url=obj.image.url,
            )
        return mark_safe(
            '<div class="bg-gray-100 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-700 flex items-center justify-center text-base-600 dark:text-base-300 text-xs">No Image</div>'
        )

    @admin.display(description=_("Category"))
    def category_name(self, obj):
        name = (
            obj.category.safe_translation_getter("name", any_language=True)
            or "Unnamed Category"
        )
        return format_html(
            '<div class="text-sm font-medium text-base-900 dark:text-base-100">{name}</div>',
            name=name,
        )

    @admin.display(description=_("Type"))
    def image_type_badge(self, obj):
        type_config = {
            "MAIN": {
                "bg": "bg-blue-50 dark:bg-blue-900",
                "text": "text-blue-700 dark:text-blue-200",
                "icon": "🖼️",
            },
            "BANNER": {
                "bg": "bg-purple-50 dark:bg-purple-900",
                "text": "text-purple-700 dark:text-purple-200",
                "icon": "🖼️",
            },
            "ICON": {
                "bg": "bg-green-50 dark:bg-green-900",
                "text": "text-green-700 dark:text-green-200",
                "icon": "🎯",
            },
        }

        config = type_config.get(
            obj.image_type,
            {
                "bg": "bg-gray-50 dark:bg-gray-900",
                "text": "text-base-700 dark:text-base-700",
                "icon": "📸",
            },
        )

        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            '{bg} {text_class} rounded-full gap-1">'
            "<span>{icon}</span>"
            "<span>{label}</span>"
            "</span>",
            bg=config["bg"],
            text_class=config["text"],
            icon=config["icon"],
            label=obj.get_image_type_display(),
        )

    @admin.display(description=_("Status"))
    def status_badge(self, obj):
        if obj.active:
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                "✅ Active"
                "</span>"
            )
        return mark_safe(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
            "❌ Inactive"
            "</span>"
        )


@admin.register(ProductImage)
class ProductImageAdmin(BaseModelAdmin, TranslatableAdmin):
    ordering_field = "sort_order"
    hide_ordering_field = True
    list_display = [
        "image_preview",
        "product_name",
        "main_badge",
        "sort_order",
        "created_at",
    ]
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

    @admin.display(description=_("Preview"))
    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{url}" '
                'style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb;" />',
                url=obj.image.url,
            )
        return mark_safe(
            '<div class="bg-gray-100 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-700 flex items-center justify-center text-base-600 dark:text-base-300 text-xs">No Image</div>'
        )

    @admin.display(description=_("Product"))
    def product_name(self, obj):
        name = (
            obj.product.safe_translation_getter("name", any_language=True)
            or "Unnamed Product"
        )
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{name}</div>'
            '<div class="text-base-600 dark:text-base-300">#{sku}</div>'
            "</div>",
            name=name,
            sku=obj.product.sku[:8],
        )

    @admin.display(description=_("Type"))
    def main_badge(self, obj):
        if obj.is_main:
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200 rounded-full">'
                "⭐ Main"
                "</span>"
            )
        return mark_safe(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            'bg-gray-50 dark:bg-gray-900 text-base-700 dark:text-base-700 rounded-full">'
            "📷 Gallery"
            "</span>"
        )
