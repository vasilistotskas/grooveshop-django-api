from datetime import timedelta
from decimal import Decimal

import admin_thumbnails
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.html import format_html
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
            return format_html(
                '<img src="{}" style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb;" />',
                obj.image.url,
            )
        return format_html(
            '<div class="bg-gray-100 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-700 flex items-center justify-center text-base-400 dark:text-base-500 text-xs">No Image</div>'
        )

    image_preview.short_description = _("Preview")


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
    inlines = [ProductImageInline, TaggedItemInline]
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
            .prefetch_related("images", "reviews", "favourited_by")
        )

    def get_prepopulated_fields(self, request, obj=None):
        return {"slug": ("name",)}

    def product_info(self, obj):
        name = (
            obj.safe_translation_getter("name", any_language=True)
            or "Untitled Product"
        )
        main_image = obj.images.filter(is_main=True).first()

        image_html = ""
        if main_image and main_image.image:
            image_html = format_html(
                '<img src="{}" style="width: 50px; height: 50px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb; margin-right: 12px;" />',
                main_image.image.url,
            )
        else:
            image_html = format_html(
                '<div style="width: 50px; height: 50px; background: #f3f4f6; border-radius: 8px; border: 1px solid #e5e7eb; margin-right: 12px; display: flex; align-items: center; justify-content: center; color: #9ca3af; font-size: 20px;">📦</div>'
            )

        return format_html(
            '<div style="display: flex; align-items: center;">'
            "{}"
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="text-base-500 dark:text-base-400">#{}</div>'
            "</div>"
            "</div>",
            image_html,
            name,
            obj.sku[:8],
        )

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

            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
                '<div class="text-xs text-base-500 dark:text-base-400">{}</div>'
                "</div>",
                category_name,
                category_path if category_path else "Root",
            )
        return format_html(
            '<span class="text-base-400 dark:text-base-500">No Category</span>'
        )

    category_display.short_description = _("Category")

    def pricing_info(self, obj):
        price = obj.price
        final_price = obj.final_price
        discount = obj.discount_percent

        price_class = (
            "text-base-500 dark:text-base-400 line-through"
            if discount > 0
            else "font-bold text-base-900 dark:text-base-100"
        )

        if discount > 0:
            return format_html(
                '<div class="text-sm">'
                '<div class="{}">{}</div>'
                '<div class="font-bold text-green-600 dark:text-green-400">{}</div>'
                '<div class="text-xs text-red-600 dark:text-red-400">-{}%</div>'
                "</div>",
                price_class,
                price,
                final_price,
                discount,
            )
        else:
            return format_html(
                '<div class="text-sm"><div class="{}">{}</div></div>',
                price_class,
                price,
            )

    pricing_info.short_description = _("Pricing")

    def stock_info(self, obj):
        stock = obj.stock

        if stock == 0:
            stock_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "❌ Out of Stock"
                "</span>"
            )
        elif stock <= 5:
            stock_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                "⚠️ Critical ({})"
                "</span>",
                stock,
            )
        elif stock <= 10:
            stock_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded-full">'
                "🔶 Low ({})"
                "</span>",
                stock,
            )
        else:
            stock_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                "✅ In Stock ({})"
                "</span>",
                stock,
            )

        return stock_badge

    stock_info.short_description = _("Stock")

    def performance_metrics(self, obj):
        views = obj.view_count
        likes = obj.likes_count
        rating = obj.review_average
        rating_formatted = "{:.1f}".format(float(rating))

        return format_html(
            '<div class="text-sm">'
            '<div class="text-base-600 dark:text-base-400">👁️ {}</div>'
            '<div class="text-base-600 dark:text-base-400">❤️ {}</div>'
            '<div class="text-base-600 dark:text-base-400">⭐ {}/10</div>'
            "</div>",
            views,
            likes,
            rating_formatted,
        )

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
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded-full border border-yellow-200 dark:border-yellow-700" title="Low stock - only {} units remaining">'
                "⚠️ Low Stock"
                "</span>".format(obj.stock)
            )

        if obj.discount_percent > 0:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full border border-orange-200 dark:border-orange-700" title="Product has {}% discount applied">'
                "🏷️ {}% OFF"
                "</span>".format(obj.discount_percent, obj.discount_percent)
            )

        thirty_days_ago = timezone.now() - timedelta(days=30)
        if obj.created_at >= thirty_days_ago:
            days_old = (timezone.now() - obj.created_at).days
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full border border-blue-200 dark:border-blue-700" title="Product added {} days ago">'
                "🆕 New"
                "</span>".format(days_old)
            )

        if hasattr(obj, "is_featured") and obj.is_featured:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-300 rounded-full border border-purple-200 dark:border-purple-700" title="This product is featured on the homepage">'
                "⭐ Featured"
                "</span>"
            )

        views = obj.view_count
        if views > 100:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-indigo-50 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-300 rounded-full border border-indigo-200 dark:border-indigo-700" title="Product has {} views - performing well">'
                "🔥 Popular"
                "</span>".format(views)
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

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="{}">{}</div>'
            "</div>",
            obj.created_at.strftime("%Y-%m-%d"),
            color,
            time_ago,
        )

    created_display.short_description = _("Created")

    def pricing_summary(self, obj):
        price = obj.price
        discount = obj.discount_percent
        final_price = obj.final_price
        discount_value = obj.discount_value
        vat_value = obj.vat_value
        vat_percent = obj.vat_percent

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Base Price:</strong></div><div>{}</div>"
            "<div><strong>Discount %:</strong></div><div>{}%</div>"
            "<div><strong>Discount Value:</strong></div><div>{}</div>"
            "<div><strong>VAT %:</strong></div><div>{}%</div>"
            "<div><strong>VAT Value:</strong></div><div>{}</div>"
            '<div><strong>Final Price:</strong></div><div class="font-bold">{}</div>'
            "</div>"
            "</div>",
            price,
            discount,
            discount_value,
            vat_percent,
            vat_value,
            final_price,
        )

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

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Total Views:</strong></div><div>{}</div>"
            "<div><strong>Likes:</strong></div><div>{}</div>"
            "<div><strong>Favorites:</strong></div><div>{}</div>"
            "<div><strong>Reviews:</strong></div><div>{}</div>"
            "<div><strong>Avg Rating:</strong></div><div>{}/10</div>"
            "<div><strong>Engagement:</strong></div><div>{}%%</div>"
            "</div>"
            "</div>",
            views,
            likes,
            favorites_count,
            review_count,
            rating_formatted,
            engagement_formatted,
        )

    performance_summary.short_description = _("Performance Summary")

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
            "<div><strong>Product Age:</strong></div><div>{}d</div>"
            "<div><strong>Last Updated:</strong></div><div>{}d ago</div>"
            "<div><strong>Stock Value:</strong></div><div>{}</div>"
            "<div><strong>Engagement Rate:</strong></div><div>{}%%</div>"
            "<div><strong>Has Images:</strong></div><div>{}</div>"
            "<div><strong>Has Reviews:</strong></div><div>{}</div>"
            "<div><strong>In Category:</strong></div><div>{}</div>"
            "<div><strong>Changed By:</strong></div><div>{}</div>"
            "</div>"
            "</div>",
            age.days,
            last_updated.days,
            obj.final_price.amount * obj.stock,
            engagement_formatted,
            "Yes" if obj.images.exists() else "No",
            "Yes" if obj.reviews.exists() else "No",
            "Yes" if obj.category else "No",
            obj.changed_by.email if obj.changed_by else "System",
        )

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
            return format_html(
                '<img src="{}" style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb;" />',
                obj.image.url,
            )
        return format_html(
            '<div class="bg-gray-100 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-700 flex items-center justify-center text-base-400 dark:text-base-500 text-xs">No Image</div>'
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

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="text-base-500 dark:text-base-400">Level: {}</div>'
            '<div class="text-xs text-base-400 dark:text-base-500">{}</div>'
            "</div>",
            name,
            level,
            path if path else "Root Category",
        )

    category_info.short_description = _("Category")

    def category_stats(self, instance):
        direct_count = getattr(instance, "products_count", 0)
        total_count = getattr(instance, "products_cumulative_count", 0)
        children_count = instance.get_children().count()

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">Direct: {}</div>'
            '<div class="text-base-600 dark:text-base-400">Total: {}</div>'
            '<div class="text-base-500 dark:text-base-500">Subcategories: {}</div>'
            "</div>",
            direct_count,
            total_count,
            children_count,
        )

    category_stats.short_description = _("Product Stats")

    def category_status(self, instance):
        if instance.active:
            status_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                "✅ Active"
                "</span>"
            )
        else:
            status_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "❌ Inactive"
                "</span>"
            )

        return status_badge

    category_status.short_description = _("Status")

    def image_preview(self, instance):
        main_image = instance.main_image
        if main_image and main_image.image:
            return format_html(
                '<img src="{}" style="width: 40px; height: 40px; object-fit: cover; border-radius: 6px; border: 1px solid #e5e7eb;" />',
                main_image.image.url,
            )
        return format_html(
            '<div class="bg-gray-100 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-700 flex items-center justify-center text-base-400 dark:text-base-500 text-xs">'
            "📁"
            "</div>"
        )

    image_preview.short_description = _("Image")

    def created_display(self, instance):
        return format_html(
            '<div class="text-sm text-base-600 dark:text-base-400">{}</div>',
            instance.created_at.strftime("%Y-%m-%d"),
        )

    created_display.short_description = _("Created")

    def category_analytics(self, instance):
        ancestors_count = instance.get_ancestors().count()
        descendants_count = instance.get_descendants().count()
        siblings_count = instance.get_siblings().count()

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Level:</strong></div><div>{}</div>"
            "<div><strong>Ancestors:</strong></div><div>{}</div>"
            "<div><strong>Descendants:</strong></div><div>{}</div>"
            "<div><strong>Siblings:</strong></div><div>{}</div>"
            "<div><strong>Direct Products:</strong></div><div>{}</div>"
            "<div><strong>Total Products:</strong></div><div>{}</div>"
            "</div>"
            "</div>",
            instance.level,
            ancestors_count,
            descendants_count,
            siblings_count,
            getattr(instance, "products_count", 0),
            getattr(instance, "products_cumulative_count", 0),
        )

    category_analytics.short_description = _("Category Analytics")

    def products_count_display(self, instance):
        count = getattr(instance, "products_count", 0)
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-semibold bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">{}</span>',
            count,
        )

    products_count_display.short_description = _("Direct Products")

    def recursive_products_display(self, instance):
        count = getattr(instance, "products_cumulative_count", 0)
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-300 rounded-full">{}</span>',
            count,
        )

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

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">Review #{}</div>'
            '<div class="text-base-600 dark:text-base-400">{}</div>'
            "</div>",
            obj.id,
            comment_preview,
        )

    review_info.short_description = _("Review")

    def product_link(self, obj):
        if obj.product:
            name = obj.product.safe_translation_getter(
                "name", any_language=True
            ) or str(obj.product.id)
            url = f"/admin/product/product/{obj.product.id}/change/"
            return format_html(
                '<div class="text-sm">'
                '<a href="{}" class="font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300">{}</a>'
                '<div class="text-base-500 dark:text-base-400">#{}</div>'
                "</div>",
                url,
                name,
                obj.product.id,
            )
        return "-"

    product_link.short_description = _("Product")

    def user_link(self, obj):
        if obj.user:
            url = f"/admin/user/useraccount/{obj.user.id}/change/"
            return format_html(
                '<div class="text-sm">'
                '<a href="{}" class="font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300">{}</a>'
                '<div class="text-base-500 dark:text-base-400">ID: {}</div>'
                "</div>",
                url,
                obj.user.email,
                obj.user.id,
            )
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

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium {}">{}/10</div>'
            '<div class="text-xs">{}</div>'
            "</div>",
            color,
            rate,
            stars[:5],
        )

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

    def created_display(self, obj):
        return format_html(
            '<div class="text-sm text-base-600 dark:text-base-400">{}</div>',
            obj.created_at.strftime("%Y-%m-%d %H:%M"),
        )

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
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="text-base-500 dark:text-base-400">ID: {}</div>'
            "</div>",
            obj.user.email,
            obj.user.id,
        )

    user_display.short_description = _("User")

    def product_display(self, obj):
        name = (
            obj.product.safe_translation_getter("name", any_language=True)
            or "Unnamed Product"
        )
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="text-base-500 dark:text-base-400">#{}</div>'
            "</div>",
            name,
            obj.product.sku[:8],
        )

    product_display.short_description = _("Product")

    def created_display(self, obj):
        return format_html(
            '<div class="text-sm text-base-600 dark:text-base-400">{}</div>',
            obj.created_at.strftime("%Y-%m-%d %H:%M"),
        )

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
            return format_html(
                '<img src="{}" style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb;" />',
                obj.image.url,
            )
        return format_html(
            '<div class="bg-gray-100 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-700 flex items-center justify-center text-base-400 dark:text-base-500 text-xs">No Image</div>'
        )

    image_preview.short_description = _("Preview")

    def category_name(self, obj):
        name = (
            obj.category.safe_translation_getter("name", any_language=True)
            or "Unnamed Category"
        )
        return format_html(
            '<div class="text-sm font-medium text-base-900 dark:text-base-100">{}</div>',
            name,
        )

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
                "icon": "🏞️",
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
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium {} {} rounded-full gap-1">'
            "<span>{}</span>"
            "<span>{}</span>"
            "</span>",
            config["bg"],
            config["text"],
            config["icon"],
            obj.get_image_type_display(),
        )

    image_type_badge.short_description = _("Type")

    def status_badge(self, obj):
        if obj.active:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
                "✅ Active"
                "</span>"
            )
        else:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full">'
                "❌ Inactive"
                "</span>"
            )

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
            return format_html(
                '<img src="{}" style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb;" />',
                obj.image.url,
            )
        return format_html(
            '<div class="bg-gray-100 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-700 flex items-center justify-center text-base-400 dark:text-base-500 text-xs">No Image</div>'
        )

    image_preview.short_description = _("Preview")

    def product_name(self, obj):
        name = (
            obj.product.safe_translation_getter("name", any_language=True)
            or "Unnamed Product"
        )
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="text-base-500 dark:text-base-400">#{}</div>'
            "</div>",
            name,
            obj.product.sku[:8],
        )

    product_name.short_description = _("Product")

    def main_badge(self, obj):
        if obj.is_main:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200 rounded-full">'
                "⭐ Main"
                "</span>"
            )
        else:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-gray-50 dark:bg-gray-900 text-base-700 dark:text-base-700 rounded-full">'
                "📷 Gallery"
                "</span>"
            )

    main_badge.short_description = _("Type")
