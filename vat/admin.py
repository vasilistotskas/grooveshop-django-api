from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
    RangeNumericFilter,
    SliderNumericFilter,
)

from vat.models import Vat


class VatRangeFilter(DropdownFilter):
    title = _("VAT Range")
    parameter_name = "vat_range"

    def lookups(self, request, model_admin):
        return [
            ("zero", _("Zero VAT (0%)")),
            ("low", _("Low VAT (0.1% - 10%)")),
            ("standard", _("Standard VAT (10% - 25%)")),
            ("high", _("High VAT (25% - 50%)")),
            ("very_high", _("Very High VAT (50%+)")),
        ]

    def queryset(self, request, queryset):
        if self.value() == "zero":
            return queryset.filter(value=0)
        elif self.value() == "low":
            return queryset.filter(value__gt=0, value__lte=10)
        elif self.value() == "standard":
            return queryset.filter(value__gt=10, value__lte=25)
        elif self.value() == "high":
            return queryset.filter(value__gt=25, value__lte=50)
        elif self.value() == "very_high":
            return queryset.filter(value__gt=50)
        return queryset


class VatUsageFilter(DropdownFilter):
    title = _("Usage Status")
    parameter_name = "usage_status"

    def lookups(self, request, model_admin):
        return [
            ("in_use", _("Currently in Use")),
            ("unused", _("Not in Use")),
            ("popular", _("Popular (5+ products)")),
            ("rare", _("Rarely Used (1-2 products)")),
        ]

    def queryset(self, request, queryset):
        if self.value() == "in_use":
            return queryset.filter(products__isnull=False).distinct()
        elif self.value() == "unused":
            return queryset.filter(products__isnull=True)
        elif self.value() == "popular":
            return queryset.annotate(product_count=Count("products")).filter(
                product_count__gte=5
            )
        elif self.value() == "rare":
            return queryset.annotate(product_count=Count("products")).filter(
                product_count__range=(1, 2)
            )
        return queryset


@admin.register(Vat)
class VatAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = False
    list_filter_submit = True
    list_filter_sheet = True

    list_display = [
        "vat_display",
        "vat_category_badge",
        "usage_metrics",
        "calculation_preview",
        "created_display",
        "updated_display",
    ]

    list_filter = [
        VatRangeFilter,
        VatUsageFilter,
        ("value", SliderNumericFilter),
        ("value", RangeNumericFilter),
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
    ]

    search_fields = ["value", "id"]

    readonly_fields = [
        "id",
        "uuid",
        "created_at",
        "updated_at",
        "usage_analytics",
        "calculation_examples",
        "products_using_vat",
    ]

    ordering = ["value"]
    list_per_page = 25

    fieldsets = (
        (
            _("VAT Configuration"),
            {
                "fields": ("value",),
                "classes": ("wide",),
                "description": _("Set the VAT percentage rate (0.0% - 100.0%)"),
            },
        ),
        (
            _("Usage Analytics"),
            {
                "fields": ("usage_analytics", "products_using_vat"),
                "classes": ("collapse",),
            },
        ),
        (
            _("Calculation Examples"),
            {
                "fields": ("calculation_examples",),
                "classes": ("collapse",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": ("id", "uuid", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(products_count=Count("products", distinct=True))

    @admin.display(description=_("VAT Rate"))
    def vat_display(self, obj):
        value = obj.value
        formatted_value = f"{value:g}%"

        if value == 0:
            color_class = "text-green-600 dark:text-green-400"
            icon = "🆓"
        elif value <= 10:
            color_class = "text-blue-600 dark:text-blue-400"
            icon = "📉"
        elif value <= 25:
            color_class = "text-yellow-600 dark:text-yellow-400"
            icon = "📊"
        elif value <= 50:
            color_class = "text-orange-600 dark:text-orange-400"
            icon = "📈"
        else:
            color_class = "text-red-600 dark:text-red-400"
            icon = "🔥"

        return format_html(
            '<div class="flex items-center gap-2">'
            '<span class="text-lg">{icon}</span>'
            '<div class="text-lg font-bold {color_class}">{value}</div>'
            "</div>",
            icon=icon,
            color_class=color_class,
            value=formatted_value,
        )

    @admin.display(description=_("Category"))
    def vat_category_badge(self, obj):
        value = obj.value

        if value == 0:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 "
                'rounded-full gap-1">'
                "<span>🆓</span><span>Tax Free</span>"
                "</span>"
            )
        if value <= 5:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                "bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 "
                'rounded-full gap-1">'
                "<span>📉</span><span>Reduced</span>"
                "</span>"
            )
        if value <= 15:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                "bg-indigo-50 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-300 "
                'rounded-full gap-1">'
                "<span>📊</span><span>Low</span>"
                "</span>"
            )
        if value <= 25:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                "bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 "
                'rounded-full gap-1">'
                "<span>⚖️</span><span>Standard</span>"
                "</span>"
            )
        if value <= 35:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                "bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 "
                'rounded-full gap-1">'
                "<span>📈</span><span>High</span>"
                "</span>"
            )
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            "bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 "
            'rounded-full gap-1">'
            "<span>🔥</span><span>Premium</span>"
            "</span>"
        )

    @admin.display(description=_("Usage"))
    def usage_metrics(self, obj):
        products_count = getattr(obj, "products_count", 0)

        if products_count == 0:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                "bg-gray-50 dark:bg-gray-900 text-gray-700 dark:text-gray-300 "
                'rounded-full">'
                "📦 Unused"
                "</span>"
            )

        if products_count <= 2:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                "bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 "
                'rounded-full">'
                "📊 {count} products"
                "</span>",
                count=products_count,
            )
        if products_count <= 10:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                "bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 "
                'rounded-full">'
                "📈 {count} products"
                "</span>",
                count=products_count,
            )
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 "
            'rounded-full">'
            "🔥 {count} products"
            "</span>",
            count=products_count,
        )

    @admin.display(description=_("Price Examples"))
    def calculation_preview(self, obj):
        base_prices = [10, 50, 100]
        examples = []
        for price in base_prices:
            vat_amount = (price * obj.value) / 100
            total = price + vat_amount
            examples.append(f"€{price} → €{total:.2f}")

        return format_html(
            '<div class="text-xs text-gray-600 dark:text-gray-400">'
            '<div class="font-mono">{examples}</div>'
            "</div>",
            examples=" | ".join(examples),
        )

    @admin.display(description=_("Created"))
    def created_display(self, obj):
        return format_html(
            '<div class="text-sm text-gray-600 dark:text-gray-400">{date}</div>',
            date=obj.created_at.strftime("%Y-%m-%d"),
        )

    @admin.display(description=_("Updated"))
    def updated_display(self, obj):
        return format_html(
            '<div class="text-sm text-gray-600 dark:text-gray-400">{date}</div>',
            date=obj.updated_at.strftime("%Y-%m-%d"),
        )

    @admin.display(description=_("Usage Analytics"))
    def usage_analytics(self, obj):
        try:
            products_count = getattr(obj, "products_count", 0)
            base_calculation = 1000
            vat_amount = (base_calculation * obj.value) / 100
            total_with_vat = base_calculation + vat_amount

            vat_formatted = f"€{vat_amount:.2f}"
            total_formatted = f"€{total_with_vat:.2f}"
            value_formatted = f"{obj.value:g}%"
            category_text = self._get_vat_category_text(obj.value)
            status_text = "In Use" if products_count > 0 else "Unused"

            return format_html(
                '<div class="text-sm">'
                '<div class="grid grid-cols-2 gap-2">'
                "<div><strong>Products Using:</strong></div><div>{products}</div>"
                "<div><strong>VAT Rate:</strong></div><div>{value}</div>"
                "<div><strong>Sample VAT on €1000:</strong></div><div>{vat}</div>"
                "<div><strong>Total with VAT:</strong></div><div>{total}</div>"
                "<div><strong>Category:</strong></div><div>{category}</div>"
                "<div><strong>Status:</strong></div><div>{status}</div>"
                "</div>"
                "</div>",
                products=products_count,
                value=value_formatted,
                vat=vat_formatted,
                total=total_formatted,
                category=category_text,
                status=status_text,
            )
        except Exception:
            return format_html(
                '<span class="text-gray-500">Data unavailable</span>'
            )

    @admin.display(description=_("Calculation Examples"))
    def calculation_examples(self, obj):
        examples = []
        base_amounts = [10, 25, 50, 100, 250, 500, 1000]

        for amount in base_amounts:
            vat_amount = (amount * obj.value) / 100
            total = amount + vat_amount
            examples.append(f"€{amount:g} + VAT = €{total:.2f}")

        items = format_html_join(
            "", "<div>{}</div>", ((example,) for example in examples)
        )
        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "{items}"
            "</div>"
            "</div>",
            items=items,
        )

    @admin.display(description=_("Products Using This VAT"))
    def products_using_vat(self, obj):
        try:
            products_count = getattr(obj, "products_count", 0)

            if products_count == 0:
                return format_html(
                    '<div class="text-sm text-gray-500 dark:text-gray-400 italic">'
                    "No products currently using this VAT rate"
                    "</div>"
                )

            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-gray-900 dark:text-gray-100">'
                "{count} products using this VAT rate"
                "</div>"
                '<div class="text-gray-500 dark:text-gray-400 mt-1">'
                "Click to view products →"
                "</div>"
                "</div>",
                count=products_count,
            )
        except Exception:
            return format_html(
                '<span class="text-gray-500">Data unavailable</span>'
            )

    def _get_vat_category_text(self, value):
        if value == 0:
            return "Tax Free"
        elif value <= 5:
            return "Reduced Rate"
        elif value <= 15:
            return "Low Rate"
        elif value <= 25:
            return "Standard Rate"
        elif value <= 35:
            return "High Rate"
        else:
            return "Premium Rate"
