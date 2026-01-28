from datetime import timedelta
from decimal import Decimal

import admin_thumbnails
from django.contrib import admin, messages
from django.db.models import Q, Sum
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from mptt.admin import DraggableMPTTAdmin
from parler.admin import TranslatableAdmin
from simple_history.admin import SimpleHistoryAdmin
from unfold.admin import ModelAdmin, TabularInline
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
from product.models.category import ProductCategory
from product.models.category_image import ProductCategoryImage
from product.models.favourite import ProductFavourite
from product.models.image import ProductImage
from product.models.product import Product
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
                queryset = queryset.with_likes_count_annotation()
                filter_kwargs = {"likes_count_annotation__gt": 10}
            case "well_reviewed":
                queryset = queryset.with_review_average_annotation()
                filter_kwargs = {"review_average_annotation__gt": 7.0}
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
        filters = {}

        queryset = queryset.with_likes_count_annotation()

        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["likes_count_annotation__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["likes_count_annotation__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class ReviewAverageFilter(RangeNumericListFilter):
    title = _("Review Rating")
    parameter_name = "review_average"

    def queryset(self, request, queryset):
        filters = {}

        queryset = queryset.with_review_average_annotation()

        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["review_average_annotation__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["review_average_annotation__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

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
            return queryset.filter(reserved_qty__gt=0).extra(
                where=["reserved_qty > stock * 0.5"]
            )

        return queryset


@admin_thumbnails.thumbnail("image")
class ProductImageInline(TabularInline):
    model = ProductImage
    extra = 0
    fields = ("image_preview", "image", "is_main")
    readonly_fields = ("image_preview",)

    tab = True
    show_change_link = True

    def image_preview(self, obj):
        if obj.image:
            safe_url = conditional_escape(obj.image.url)
            html = (
                f'<img src="{safe_url}" '
                'style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb;" />'
            )
            return mark_safe(html)
        return mark_safe(
            '<div class="bg-gray-100 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-700 flex items-center justify-center text-base-600 dark:text-base-300 text-xs">No Image</div>'
        )

    image_preview.short_description = _("Preview")


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

    def reservation_info(self, obj):
        """Display reservation ID and creation time."""
        created = obj.created_at.strftime("%Y-%m-%d %H:%M")
        safe_id = conditional_escape(str(obj.id))
        safe_created = conditional_escape(created)

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">#{safe_id}</div>'
            f'<div class="text-xs text-base-600 dark:text-base-300">{safe_created}</div>'
            "</div>"
        )
        return mark_safe(html)

    reservation_info.short_description = _("Reservation")

    def session_info(self, obj):
        """Display user or session information."""
        if obj.reserved_by:
            user_display = obj.reserved_by.email or obj.reserved_by.username
            safe_user = conditional_escape(user_display)
            html = (
                '<div class="text-sm">'
                f'<div class="font-medium text-base-900 dark:text-base-100">👤 {safe_user}</div>'
                "</div>"
            )
        else:
            session_short = (
                obj.session_id[:8]
                if len(obj.session_id) > 8
                else obj.session_id
            )
            safe_session = conditional_escape(session_short)
            html = (
                '<div class="text-sm">'
                f'<div class="text-base-600 dark:text-base-400">🔒 Guest: {safe_session}...</div>'
                "</div>"
            )
        return mark_safe(html)

    session_info.short_description = _("Reserved By")

    def expires_display(self, obj):
        """Display expiration time with countdown."""
        now = timezone.now()
        time_left = obj.expires_at - now
        minutes_left = int(time_left.total_seconds() / 60)

        safe_minutes = conditional_escape(str(minutes_left))

        if minutes_left <= 2:
            color_class = "text-red-600 dark:text-red-400"
            icon = "🔴"
        elif minutes_left <= 5:
            color_class = "text-orange-600 dark:text-orange-400"
            icon = "🟠"
        else:
            color_class = "text-green-600 dark:text-green-400"
            icon = "🟢"

        html = (
            f'<div class="text-sm {color_class}">'
            f"{icon} {safe_minutes} min left"
            "</div>"
        )
        return mark_safe(html)

    expires_display.short_description = _("Expires In")

    def status_badge(self, obj):
        """Display reservation status badge."""
        if obj.order:
            order_id = obj.order.id
            safe_order_id = conditional_escape(str(order_id))
            badge = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">'
                f"📦 Order #{safe_order_id}"
                "</span>"
            )
        else:
            badge = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded-full">'
                "⏳ Pending Checkout"
                "</span>"
            )
        return mark_safe(badge)

    status_badge.short_description = _("Status")


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

    def operation_display(self, obj):
        """Display operation type with icon."""
        operation_icons = {
            "RESERVE": "🔒",
            "RELEASE": "🔓",
            "DECREMENT": "📉",
            "INCREMENT": "📈",
        }
        icon = operation_icons.get(obj.operation_type, "📝")
        safe_op = conditional_escape(obj.get_operation_type_display())

        color_map = {
            "RESERVE": "text-blue-600 dark:text-blue-400",
            "RELEASE": "text-green-600 dark:text-green-400",
            "DECREMENT": "text-red-600 dark:text-red-400",
            "INCREMENT": "text-green-600 dark:text-green-400",
        }
        color_class = color_map.get(
            obj.operation_type, "text-base-600 dark:text-base-400"
        )

        html = (
            f'<div class="text-sm {color_class} font-medium">'
            f"{icon} {safe_op}"
            "</div>"
        )
        return mark_safe(html)

    operation_display.short_description = _("Operation")

    def quantity_change(self, obj):
        """Display quantity delta with +/- indicator."""
        delta = obj.quantity_delta
        safe_delta = conditional_escape(str(abs(delta)))

        if delta > 0:
            html = (
                '<span class="text-sm font-medium text-green-600 dark:text-green-400">'
                f"+{safe_delta}"
                "</span>"
            )
        elif delta < 0:
            html = (
                '<span class="text-sm font-medium text-red-600 dark:text-red-400">'
                f"-{safe_delta}"
                "</span>"
            )
        else:
            html = '<span class="text-sm text-base-600 dark:text-base-300">0</span>'

        return mark_safe(html)

    quantity_change.short_description = _("Change")

    def stock_levels(self, obj):
        """Display before/after stock levels."""
        safe_before = conditional_escape(str(obj.stock_before))
        safe_after = conditional_escape(str(obj.stock_after))

        html = (
            '<div class="text-sm text-base-600 dark:text-base-400">'
            f"{safe_before} → {safe_after}"
            "</div>"
        )
        return mark_safe(html)

    stock_levels.short_description = _("Stock Level")

    def order_link(self, obj):
        """Display linked order if any."""
        if obj.order:
            order_id = obj.order.id
            safe_order_id = conditional_escape(str(order_id))
            # Create admin URL for order
            url = reverse_lazy("admin:order_order_change", args=[order_id])
            safe_url = conditional_escape(str(url))

            html = (
                f'<a href="{safe_url}" class="text-sm text-blue-600 dark:text-blue-400 hover:underline">'
                f"Order #{safe_order_id}"
                "</a>"
            )
            return mark_safe(html)

        reason = obj.reason or "N/A"
        safe_reason = conditional_escape(reason[:45])
        return mark_safe(
            f'<span class="text-sm text-base-600 dark:text-base-300">{safe_reason}</span>'
        )

    order_link.short_description = _("Related Order")

    def performed_by_display(self, obj):
        """Display who performed the operation."""
        if obj.performed_by:
            user_display = obj.performed_by.email or obj.performed_by.username
            safe_user = conditional_escape(user_display[:20])
            html = f'<span class="text-sm text-base-600 dark:text-base-400">{safe_user}</span>'
        else:
            html = '<span class="text-sm text-base-600 dark:text-base-300">System</span>'

        return mark_safe(html)

    performed_by_display.short_description = _("By")

    def timestamp_display(self, obj):
        """Display operation timestamp."""
        timestamp = obj.created_at.strftime("%m/%d %H:%M")
        safe_timestamp = conditional_escape(timestamp)

        html = f'<span class="text-sm text-base-600 dark:text-base-300">{safe_timestamp}</span>'
        return mark_safe(html)

    timestamp_display.short_description = _("Time")


@admin.register(Product)
class ProductAdmin(
    TranslatableAdmin, ExportModelAdmin, SimpleHistoryAdmin, ModelAdmin
):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

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
    ]
    inlines = [
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
    list_per_page = 25
    actions = [
        "make_active",
        "make_inactive",
        "apply_custom_discount",
        "clear_discount",
    ]
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
                "classes": ("wide",),
            },
        ),
        (
            _("Pricing"),
            {
                "fields": (
                    "price",
                    "discount_percent",
                    "vat",
                    "pricing_summary",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Inventory"),
            {
                "fields": (
                    "stock",
                    "weight",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Stock Management & Audit"),
            {
                "fields": ("stock_reservation_summary",),
                "classes": ("collapse",),
                "description": _(
                    "View active stock reservations and recent stock operations for this product."
                ),
            },
        ),
        (
            _("SEO & Marketing"),
            {
                "fields": (
                    "slug",
                    "seo_title",
                    "seo_description",
                    "seo_keywords",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            _("Performance Metrics"),
            {
                "fields": (
                    "view_count",
                    "likes_count",
                    "performance_summary",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            _("Analytics"),
            {
                "fields": ("product_analytics",),
                "classes": ("collapse",),
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
                "classes": ("collapse",),
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
        return (
            super()
            .get_queryset(request)
            .with_likes_count_annotation()
            .with_review_average_annotation()
            .select_related("category", "vat", "changed_by")
            .prefetch_related(
                "images",
                "reviews",
                "favourited_by",
                "stock_reservations",
                "stock_logs",
            )
        )

    def get_prepopulated_fields(self, request, obj=None):
        return {"slug": ("name",)}

    def product_info(self, obj):
        name = (
            obj.safe_translation_getter("name", any_language=True)
            or "Untitled Product"
        )
        main_image = obj.images.filter(is_main=True).first()

        safe_name = conditional_escape(name)
        safe_sku = conditional_escape(obj.sku[:8])

        if main_image and main_image.image:
            safe_url = conditional_escape(main_image.image.url)
            image_html = (
                f'<img src="{safe_url}" '
                'style="width: 50px; height: 50px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb; margin-right: 12px;" />'
            )
        else:
            image_html = '<div style="width: 50px; height: 50px; background: #f3f4f6; border-radius: 8px; border: 1px solid #e5e7eb; margin-right: 12px; display: flex; align-items: center; justify-content: center; color: #9ca3af; font-size: 20px;">📦</div>'

        html = (
            '<div style="display: flex; align-items: center;">'
            f"{image_html}"
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_name}</div>'
            f'<div class="text-base-600 dark:text-base-300">#{safe_sku}</div>'
            "</div>"
            "</div>"
        )
        return mark_safe(html)

    product_info.short_description = _("Product")

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

            safe_name = conditional_escape(category_name)
            safe_path = conditional_escape(
                category_path if category_path else "Root"
            )

            html = (
                '<div class="text-sm">'
                f'<div class="font-medium text-base-900 dark:text-base-100">{safe_name}</div>'
                f'<div class="text-xs text-base-600 dark:text-base-300">{safe_path}</div>'
                "</div>"
            )
            return mark_safe(html)
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300">No Category</span>'
        )

    category_display.short_description = _("Category")

    def pricing_info(self, obj):
        price = obj.price
        final_price = obj.final_price
        discount = obj.discount_percent

        safe_price = conditional_escape(str(price))
        safe_final = conditional_escape(str(final_price))
        safe_discount = conditional_escape(str(discount))

        price_class = (
            "text-base-600 dark:text-base-300 line-through"
            if discount > 0
            else "font-bold text-base-900 dark:text-base-100"
        )

        if discount > 0:
            html = (
                '<div class="text-sm">'
                f'<div class="{price_class}">{safe_price}</div>'
                f'<div class="font-bold text-green-600 dark:text-green-400">{safe_final}</div>'
                f'<div class="text-xs text-red-600 dark:text-red-400">-{safe_discount}%</div>'
                "</div>"
            )
        else:
            html = (
                '<div class="text-sm">'
                f'<div class="{price_class}">{safe_price}</div>'
                "</div>"
            )
        return mark_safe(html)

    pricing_info.short_description = _("Pricing")

    def stock_info(self, obj):
        from order.models.stock_reservation import StockReservation

        stock = obj.stock
        safe_stock = conditional_escape(str(stock))

        # Calculate reserved stock from active reservations
        now = timezone.now()
        reserved_qty = (
            StockReservation.objects.filter(
                product=obj, expires_at__gt=now, consumed=False
            ).aggregate(total=Sum("quantity"))["total"]
            or 0
        )

        available_stock = stock - reserved_qty
        safe_reserved = conditional_escape(str(reserved_qty))
        safe_available = conditional_escape(str(available_stock))

        if stock == 0:
            stock_badge = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "❌ Out of Stock"
                "</span>"
            )
        elif stock <= 5:
            stock_badge = (
                f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                f'bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                f"⚠️ Critical ({safe_stock})"
                "</span>"
            )
        elif stock <= 10:
            stock_badge = (
                f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                f'bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded-full">'
                f"🔶 Low ({safe_stock})"
                "</span>"
            )
        else:
            stock_badge = (
                f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                f'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                f"✅ In Stock ({safe_stock})"
                "</span>"
            )

        # Add reservation info if any
        if reserved_qty > 0:
            reservation_info = (
                f'<div class="text-xs text-blue-600 dark:text-blue-400 mt-1">'
                f"🔒 {safe_reserved} reserved"
                "</div>"
                f'<div class="text-xs text-green-600 dark:text-green-400">'
                f"✓ {safe_available} available"
                "</div>"
            )
        else:
            reservation_info = ""

        return mark_safe(
            f'<div class="text-sm">{stock_badge}{reservation_info}</div>'
        )

    stock_info.short_description = _("Stock")

    def performance_metrics(self, obj):
        views = obj.view_count
        likes = obj.likes_count
        rating = obj.review_average
        rating_formatted = "{:.1f}".format(float(rating))

        safe_views = conditional_escape(str(views))
        safe_likes = conditional_escape(str(likes))
        safe_rating = conditional_escape(rating_formatted)

        html = (
            '<div class="text-sm">'
            f'<div class="text-base-600 dark:text-base-400">👁️ {safe_views}</div>'
            f'<div class="text-base-600 dark:text-base-400">❤️ {safe_likes}</div>'
            f'<div class="text-base-600 dark:text-base-400">⭐ {safe_rating}/10</div>'
            "</div>"
        )
        return mark_safe(html)

    performance_metrics.short_description = _("Performance")

    def status_badges(self, obj):
        badges = []

        if obj.active:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full border border-green-200 dark:border-green-700" title="Product is active and visible to customers">'
                "✅ Active"
                "</span>"
            )
        else:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full border border-red-200 dark:border-red-700" title="Product is inactive and hidden from customers">'
                "❌ Inactive"
                "</span>"
            )

        if obj.stock <= 0:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-gray-50 dark:bg-gray-900 text-base-700 dark:text-base-300 rounded-full border border-gray-200 dark:border-gray-700" title="Product is out of stock">'
                "📦 Out of Stock"
                "</span>"
            )
        elif obj.stock <= 10:
            safe_stock = conditional_escape(str(obj.stock))
            badges.append(
                f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded-full border border-yellow-200 dark:border-yellow-700" title="Low stock - only {safe_stock} units remaining">'
                "⚠️ Low Stock"
                "</span>"
            )

        if obj.discount_percent > 0:
            safe_discount = conditional_escape(str(obj.discount_percent))
            badges.append(
                f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full border border-orange-200 dark:border-orange-700" title="Product has {safe_discount}% discount applied">'
                f"🏷️ {safe_discount}% OFF"
                "</span>"
            )

        thirty_days_ago = timezone.now() - timedelta(days=30)
        if obj.created_at >= thirty_days_ago:
            days_old = (timezone.now() - obj.created_at).days
            safe_days = conditional_escape(str(days_old))
            badges.append(
                f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full border border-blue-200 dark:border-blue-700" title="Product added {safe_days} days ago">'
                "🆕 New"
                "</span>"
            )

        if hasattr(obj, "is_featured") and obj.is_featured:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-300 rounded-full border border-purple-200 dark:border-purple-700" title="This product is featured on the homepage">'
                "⭐ Featured"
                "</span>"
            )

        views = obj.view_count
        if views > 100:
            safe_views = conditional_escape(str(views))
            badges.append(
                f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-indigo-50 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-300 rounded-full border border-indigo-200 dark:border-indigo-700" title="Product has {safe_views} views - performing well">'
                "🔥 Popular"
                "</span>"
            )

        return mark_safe(
            '<div class="flex flex-wrap gap-1">' + "".join(badges) + "</div>"
        )

    status_badges.short_description = _("Status")

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

        safe_date = conditional_escape(obj.created_at.strftime("%Y-%m-%d"))
        safe_time = conditional_escape(time_ago)

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_date}</div>'
            f'<div class="{color}">{safe_time}</div>'
            "</div>"
        )
        return mark_safe(html)

    created_display.short_description = _("Created")

    def pricing_summary(self, obj):
        price = obj.price
        discount = obj.discount_percent
        final_price = obj.final_price
        discount_value = obj.discount_value
        vat_value = obj.vat_value
        vat_percent = obj.vat_percent

        safe_price = conditional_escape(str(price))
        safe_discount = conditional_escape(str(discount))
        safe_discount_val = conditional_escape(str(discount_value))
        safe_vat_percent = conditional_escape(str(vat_percent))
        safe_vat_value = conditional_escape(str(vat_value))
        safe_final = conditional_escape(str(final_price))

        html = (
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Base Price:</strong></div><div>{safe_price}</div>"
            f"<div><strong>Discount %:</strong></div><div>{safe_discount}%</div>"
            f"<div><strong>Discount Value:</strong></div><div>{safe_discount_val}</div>"
            f"<div><strong>VAT %:</strong></div><div>{safe_vat_percent}%</div>"
            f"<div><strong>VAT Value:</strong></div><div>{safe_vat_value}</div>"
            f'<div><strong>Final Price:</strong></div><div class="font-bold">{safe_final}</div>'
            "</div>"
            "</div>"
        )
        return mark_safe(html)

    pricing_summary.short_description = _("Pricing Summary")

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

        safe_views = conditional_escape(str(views))
        safe_likes = conditional_escape(str(likes))
        safe_favorites = conditional_escape(str(favorites_count))
        safe_reviews = conditional_escape(str(review_count))
        safe_rating = conditional_escape(rating_formatted)
        safe_engagement = conditional_escape(engagement_formatted)

        html = (
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Total Views:</strong></div><div>{safe_views}</div>"
            f"<div><strong>Likes:</strong></div><div>{safe_likes}</div>"
            f"<div><strong>Favorites:</strong></div><div>{safe_favorites}</div>"
            f"<div><strong>Reviews:</strong></div><div>{safe_reviews}</div>"
            f"<div><strong>Avg Rating:</strong></div><div>{safe_rating}/10</div>"
            f"<div><strong>Engagement:</strong></div><div>{safe_engagement}%</div>"
            "</div>"
            "</div>"
        )
        return mark_safe(html)

    performance_summary.short_description = _("Performance Summary")

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

        safe_stock = conditional_escape(str(obj.stock))
        safe_reserved = conditional_escape(str(reserved_qty))
        safe_available = conditional_escape(str(available_stock))
        safe_res_count = conditional_escape(str(reservation_count))
        safe_res_pct = conditional_escape(f"{reservation_pct:.1f}")
        safe_ops_count = conditional_escape(str(operations_count))
        safe_reserve = conditional_escape(str(reserve_count))
        safe_release = conditional_escape(str(release_count))
        safe_decrement = conditional_escape(str(decrement_count))
        safe_increment = conditional_escape(str(increment_count))

        # Color coding for reservation percentage
        if reservation_pct > 50:
            pct_color = "text-red-600 dark:text-red-400"
        elif reservation_pct > 25:
            pct_color = "text-orange-600 dark:text-orange-400"
        else:
            pct_color = "text-green-600 dark:text-green-400"

        html = (
            '<div class="text-sm">'
            '<div class="mb-3">'
            '<h4 class="font-semibold text-base-900 dark:text-base-100 mb-2">Current Stock Status</h4>'
            '<div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Total Stock:</strong></div><div>{safe_stock}</div>"
            f'<div><strong>Reserved:</strong></div><div class="text-blue-600 dark:text-blue-400">{safe_reserved}</div>'
            f'<div><strong>Available:</strong></div><div class="text-green-600 dark:text-green-400">{safe_available}</div>'
            f"<div><strong>Active Reservations:</strong></div><div>{safe_res_count}</div>"
            f'<div><strong>Reserved %:</strong></div><div class="{pct_color} font-medium">{safe_res_pct}%</div>'
            "</div>"
            "</div>"
            "<div>"
            '<h4 class="font-semibold text-base-900 dark:text-base-100 mb-2">Recent Activity (7 days)</h4>'
            '<div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Total Operations:</strong></div><div>{safe_ops_count}</div>"
            f"<div><strong>🔒 Reserves:</strong></div><div>{safe_reserve}</div>"
            f"<div><strong>🔓 Releases:</strong></div><div>{safe_release}</div>"
            f"<div><strong>📉 Decrements:</strong></div><div>{safe_decrement}</div>"
            f"<div><strong>📈 Increments:</strong></div><div>{safe_increment}</div>"
            "</div>"
            "</div>"
            "</div>"
        )
        return mark_safe(html)

    stock_reservation_summary.short_description = _("Stock Reservation Summary")

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

        safe_age = conditional_escape(str(age.days))
        safe_updated = conditional_escape(str(last_updated.days))
        safe_stock_value = conditional_escape(
            str(obj.final_price.amount * obj.stock)
        )
        safe_engagement = conditional_escape(engagement_formatted)
        has_images = "Yes" if obj.images.exists() else "No"
        has_reviews = "Yes" if obj.reviews.exists() else "No"
        in_category = "Yes" if obj.category else "No"
        safe_changed_by = conditional_escape(
            obj.changed_by.email if obj.changed_by else "System"
        )

        html = (
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Product Age:</strong></div><div>{safe_age}d</div>"
            f"<div><strong>Last Updated:</strong></div><div>{safe_updated}d ago</div>"
            f"<div><strong>Stock Value:</strong></div><div>{safe_stock_value}</div>"
            f"<div><strong>Engagement Rate:</strong></div><div>{safe_engagement}%</div>"
            f"<div><strong>Has Images:</strong></div><div>{has_images}</div>"
            f"<div><strong>Has Reviews:</strong></div><div>{has_reviews}</div>"
            f"<div><strong>In Category:</strong></div><div>{in_category}</div>"
            f"<div><strong>Changed By:</strong></div><div>{safe_changed_by}</div>"
            "</div>"
            "</div>"
        )
        return mark_safe(html)

    product_analytics.short_description = _("Product Analytics")

    @action(
        description=_("Activate selected products"),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def make_active(self, request, queryset):
        updated = queryset.update(active=True)
        self.message_user(
            request,
            ngettext(
                _("%(count)d product was successfully activated."),
                _("%(count)d products were successfully activated."),
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=_("Deactivate selected products"),
        variant=ActionVariant.WARNING,
        icon="cancel",
    )
    def make_inactive(self, request, queryset):
        updated = queryset.update(active=False)
        self.message_user(
            request,
            ngettext(
                _("%(count)d product was successfully deactivated."),
                _("%(count)d products were successfully deactivated."),
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=_("Apply custom discount to selected products"),
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
                        _(
                            "Applied %(discount)s%% discount to %(count)d product."
                        ),
                        _(
                            "Applied %(discount)s%% discount to %(count)d products."
                        ),
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
        description=_("Clear discount from selected products"),
        variant=ActionVariant.DANGER,
        icon="cancel",
    )
    def clear_discount(self, request, queryset):
        updated = queryset.update(discount_percent=Decimal("0.0"))
        self.message_user(
            request,
            ngettext(
                _("%(count)d product's discount was cleared."),
                _("%(count)d products' discounts were cleared."),
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )


@admin_thumbnails.thumbnail("image")
class ProductCategoryImageInline(TabularInline):
    model = ProductCategoryImage
    extra = 0
    fields = ("image_preview", "image", "image_type", "active")
    readonly_fields = ("image_preview",)

    tab = True
    show_change_link = True

    def image_preview(self, obj):
        if obj.image:
            safe_url = conditional_escape(obj.image.url)
            html = (
                f'<img src="{safe_url}" '
                'style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb;" />'
            )
            return mark_safe(html)
        return mark_safe(
            '<div class="bg-gray-100 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-700 flex items-center justify-center text-base-600 dark:text-base-300 text-xs">No Image</div>'
        )

    image_preview.short_description = _("Preview")


@admin.register(ProductCategory)
class ProductCategoryAdmin(ModelAdmin, TranslatableAdmin, DraggableMPTTAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    mptt_indent_field = "translations__name"
    list_per_page = 25
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

    def category_info(self, instance):
        name = (
            instance.safe_translation_getter("name", any_language=True)
            or "Unnamed Category"
        )
        level = instance.level
        path = " → ".join(
            [
                cat.safe_translation_getter("name", any_language=True)
                or "Unnamed"
                for cat in instance.get_ancestors()
            ]
        )

        safe_name = conditional_escape(name)
        safe_level = conditional_escape(str(level))
        safe_path = conditional_escape(path if path else "Root Category")

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_name}</div>'
            f'<div class="text-base-600 dark:text-base-300">Level: {safe_level}</div>'
            f'<div class="text-xs text-base-600 dark:text-base-300">{safe_path}</div>'
            "</div>"
        )
        return mark_safe(html)

    category_info.short_description = _("Category")

    def category_stats(self, instance):
        direct_count = getattr(instance, "products_count", 0)
        total_count = getattr(instance, "products_cumulative_count", 0)
        children_count = instance.get_children().count()

        safe_direct = conditional_escape(str(direct_count))
        safe_total = conditional_escape(str(total_count))
        safe_children = conditional_escape(str(children_count))

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">Direct: {safe_direct}</div>'
            f'<div class="text-base-700 dark:text-base-200">Total: {safe_total}</div>'
            f'<div class="text-base-600 dark:text-base-300">Subcategories: {safe_children}</div>'
            "</div>"
        )
        return mark_safe(html)

    category_stats.short_description = _("Product Stats")

    def category_status(self, instance):
        if instance.active:
            status_badge = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                "✅ Active"
                "</span>"
            )
        else:
            status_badge = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "❌ Inactive"
                "</span>"
            )

        return mark_safe(status_badge)

    category_status.short_description = _("Status")

    def image_preview(self, instance):
        main_image = instance.main_image
        if main_image and main_image.image:
            safe_url = conditional_escape(main_image.image.url)
            html = (
                f'<img src="{safe_url}" '
                'style="width: 40px; height: 40px; object-fit: cover; border-radius: 6px; border: 1px solid #e5e7eb;" />'
            )
            return mark_safe(html)
        return mark_safe(
            '<div class="bg-gray-100 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-700 flex items-center justify-center text-base-600 dark:text-base-300 text-xs">'
            "🖼"
            "</div>"
        )

    image_preview.short_description = _("Image")

    def created_display(self, instance):
        safe_date = conditional_escape(instance.created_at.strftime("%Y-%m-%d"))
        html = f'<div class="text-sm text-base-600 dark:text-base-400">{safe_date}</div>'
        return mark_safe(html)

    created_display.short_description = _("Created")

    def category_analytics(self, instance):
        ancestors_count = instance.get_ancestors().count()
        descendants_count = instance.get_descendants().count()
        siblings_count = instance.get_siblings().count()

        safe_level = conditional_escape(str(instance.level))
        safe_ancestors = conditional_escape(str(ancestors_count))
        safe_descendants = conditional_escape(str(descendants_count))
        safe_siblings = conditional_escape(str(siblings_count))
        safe_direct = conditional_escape(
            str(getattr(instance, "products_count", 0))
        )
        safe_total = conditional_escape(
            str(getattr(instance, "products_cumulative_count", 0))
        )

        html = (
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Level:</strong></div><div>{safe_level}</div>"
            f"<div><strong>Ancestors:</strong></div><div>{safe_ancestors}</div>"
            f"<div><strong>Descendants:</strong></div><div>{safe_descendants}</div>"
            f"<div><strong>Siblings:</strong></div><div>{safe_siblings}</div>"
            f"<div><strong>Direct Products:</strong></div><div>{safe_direct}</div>"
            f"<div><strong>Total Products:</strong></div><div>{safe_total}</div>"
            "</div>"
            "</div>"
        )
        return mark_safe(html)

    category_analytics.short_description = _("Category Analytics")

    def products_count_display(self, instance):
        count = getattr(instance, "products_count", 0)
        safe_count = conditional_escape(str(count))
        html = (
            f'<span class="inline-flex items-center px-2 py-1 text-xs font-semibold '
            f'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">'
            f"{safe_count}"
            "</span>"
        )
        return mark_safe(html)

    products_count_display.short_description = _("Direct Products")

    def recursive_products_display(self, instance):
        count = getattr(instance, "products_cumulative_count", 0)
        safe_count = conditional_escape(str(count))
        html = (
            f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            f'bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-300 rounded-full">'
            f"{safe_count}"
            "</span>"
        )
        return mark_safe(html)

    recursive_products_display.short_description = _("Total Products")


@admin.register(ProductReview)
class ProductReviewAdmin(ModelAdmin, TranslatableAdmin):
    compressed_fields = True
    list_fullwidth = True
    list_filter_sheet = True

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
        ("product", RelatedDropdownFilter),
        ("user", RelatedDropdownFilter),
    ]
    actions = [
        "approve_reviews",
        "reject_reviews",
        "make_published",
        "make_unpublished",
    ]
    search_fields = [
        "translations__comment",
        "user__email",
        "user__username",
        "product__translations__name",
    ]
    list_select_related = ["product", "user"]
    readonly_fields = ("created_at", "updated_at", "uuid")
    list_filter_submit = True

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

    def review_info(self, obj):
        comment = (
            obj.safe_translation_getter("comment", any_language=True)
            or "No comment"
        )
        comment_preview = (
            comment[:100] + "..." if len(comment) > 100 else comment
        )

        safe_id = conditional_escape(str(obj.id))
        safe_comment = conditional_escape(comment_preview)

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">Review #{safe_id}</div>'
            f'<div class="text-base-600 dark:text-base-400">{safe_comment}</div>'
            "</div>"
        )
        return mark_safe(html)

    review_info.short_description = _("Review")

    def product_link(self, obj):
        if obj.product:
            name = obj.product.safe_translation_getter(
                "name", any_language=True
            ) or str(obj.product.id)
            safe_url = conditional_escape(
                f"/admin/product/product/{obj.product.id}/change/"
            )
            safe_name = conditional_escape(name)
            safe_id = conditional_escape(str(obj.product.id))

            html = (
                '<div class="text-sm">'
                f'<a href="{safe_url}" class="font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300">{safe_name}</a>'
                f'<div class="text-base-600 dark:text-base-300">#{safe_id}</div>'
                "</div>"
            )
            return mark_safe(html)
        return "-"

    product_link.short_description = _("Product")

    def user_link(self, obj):
        if obj.user:
            safe_url = conditional_escape(
                f"/admin/user/useraccount/{obj.user.id}/change/"
            )
            safe_email = conditional_escape(obj.user.email)
            safe_id = conditional_escape(str(obj.user.id))

            html = (
                '<div class="text-sm">'
                f'<a href="{safe_url}" class="font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300">{safe_email}</a>'
                f'<div class="text-base-600 dark:text-base-300">ID: {safe_id}</div>'
                "</div>"
            )
            return mark_safe(html)
        return "-"

    user_link.short_description = _("User")

    def rating_display(self, obj):
        rate = obj.rate
        stars = "⭐" * rate + "☆" * (10 - rate)

        if rate >= 8:
            color = "text-green-600 dark:text-green-400"
        elif rate >= 6:
            color = "text-yellow-600 dark:text-yellow-400"
        else:
            color = "text-red-600 dark:text-red-400"

        safe_rate = conditional_escape(str(rate))
        safe_stars = conditional_escape(stars[:5])

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium {color}">{safe_rate}/10</div>'
            f'<div class="text-xs">{safe_stars}</div>'
            "</div>"
        )
        return mark_safe(html)

    rating_display.short_description = _("Rating")

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

    def created_display(self, obj):
        safe_date = conditional_escape(
            obj.created_at.strftime("%Y-%m-%d %H:%M")
        )
        html = f'<div class="text-sm text-base-600 dark:text-base-400">{safe_date}</div>'
        return mark_safe(html)

    created_display.short_description = _("Created")

    @action(
        description=_("Approve selected reviews"),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def approve_reviews(self, request, queryset):
        updated = queryset.update(status=ReviewStatus.TRUE)
        self.message_user(
            request,
            ngettext(
                _("%(count)d review was approved."),
                _("%(count)d reviews were approved."),
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=_("Reject selected reviews"),
        variant=ActionVariant.DANGER,
        icon="cancel",
    )
    def reject_reviews(self, request, queryset):
        updated = queryset.update(status=ReviewStatus.FALSE)
        self.message_user(
            request,
            ngettext(
                _("%(count)d review was rejected."),
                _("%(count)d reviews were rejected."),
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
                _("%(count)d comment was successfully marked as published."),
                _("%(count)d comments were successfully marked as published."),
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
                _("%(count)d comment was successfully marked as unpublished."),
                _(
                    "%(count)d comments were successfully marked as unpublished."
                ),
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )


@admin.register(ProductFavourite)
class ProductFavouriteAdmin(ModelAdmin):
    compressed_fields = True
    list_fullwidth = True

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

    def user_display(self, obj):
        safe_email = conditional_escape(obj.user.email)
        safe_id = conditional_escape(str(obj.user.id))

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_email}</div>'
            f'<div class="text-base-600 dark:text-base-300">ID: {safe_id}</div>'
            "</div>"
        )
        return mark_safe(html)

    user_display.short_description = _("User")

    def product_display(self, obj):
        name = (
            obj.product.safe_translation_getter("name", any_language=True)
            or "Unnamed Product"
        )
        safe_name = conditional_escape(name)
        safe_sku = conditional_escape(obj.product.sku[:8])

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_name}</div>'
            f'<div class="text-base-600 dark:text-base-300">#{safe_sku}</div>'
            "</div>"
        )
        return mark_safe(html)

    product_display.short_description = _("Product")

    def created_display(self, obj):
        safe_date = conditional_escape(
            obj.created_at.strftime("%Y-%m-%d %H:%M")
        )
        html = f'<div class="text-sm text-base-600 dark:text-base-400">{safe_date}</div>'
        return mark_safe(html)

    created_display.short_description = _("Created")


@admin.register(ProductCategoryImage)
class ProductCategoryImageAdmin(ModelAdmin, TranslatableAdmin):
    compressed_fields = True
    list_fullwidth = True

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
    readonly_fields = ("created_at", "updated_at", "uuid", "sort_order")
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

    def image_preview(self, obj):
        if obj.image:
            safe_url = conditional_escape(obj.image.url)
            html = (
                f'<img src="{safe_url}" '
                'style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb;" />'
            )
            return mark_safe(html)
        return mark_safe(
            '<div class="bg-gray-100 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-700 flex items-center justify-center text-base-600 dark:text-base-300 text-xs">No Image</div>'
        )

    image_preview.short_description = _("Preview")

    def category_name(self, obj):
        name = (
            obj.category.safe_translation_getter("name", any_language=True)
            or "Unnamed Category"
        )
        safe_name = conditional_escape(name)
        html = f'<div class="text-sm font-medium text-base-900 dark:text-base-100">{safe_name}</div>'
        return mark_safe(html)

    category_name.short_description = _("Category")

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

        safe_type = conditional_escape(obj.get_image_type_display())

        html = (
            f'<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            f'{config["bg"]} {config["text"]} rounded-full gap-1">'
            f"<span>{config['icon']}</span>"
            f"<span>{safe_type}</span>"
            "</span>"
        )
        return mark_safe(html)

    image_type_badge.short_description = _("Type")

    def status_badge(self, obj):
        if obj.active:
            html = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                "✅ Active"
                "</span>"
            )
        else:
            html = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "❌ Inactive"
                "</span>"
            )
        return mark_safe(html)

    status_badge.short_description = _("Status")


@admin.register(ProductImage)
class ProductImageAdmin(ModelAdmin, TranslatableAdmin):
    compressed_fields = True
    list_fullwidth = True

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
    readonly_fields = ("created_at", "updated_at", "uuid", "sort_order")
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

    def image_preview(self, obj):
        if obj.image:
            safe_url = conditional_escape(obj.image.url)
            html = (
                f'<img src="{safe_url}" '
                'style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb;" />'
            )
            return mark_safe(html)
        return mark_safe(
            '<div class="bg-gray-100 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-700 flex items-center justify-center text-base-600 dark:text-base-300 text-xs">No Image</div>'
        )

    image_preview.short_description = _("Preview")

    def product_name(self, obj):
        name = (
            obj.product.safe_translation_getter("name", any_language=True)
            or "Unnamed Product"
        )
        safe_name = conditional_escape(name)
        safe_sku = conditional_escape(obj.product.sku[:8])

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_name}</div>'
            f'<div class="text-base-600 dark:text-base-300">#{safe_sku}</div>'
            "</div>"
        )
        return mark_safe(html)

    product_name.short_description = _("Product")

    def main_badge(self, obj):
        if obj.is_main:
            html = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200 rounded-full">'
                "⭐ Main"
                "</span>"
            )
        else:
            html = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-gray-50 dark:bg-gray-900 text-base-700 dark:text-base-700 rounded-full">'
                "📷 Gallery"
                "</span>"
            )
        return mark_safe(html)

    main_badge.short_description = _("Type")
