from django.contrib import admin
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
    RangeNumericFilter,
)
from unfold.decorators import display

from admin.base import BaseModelAdmin
from vat.models import Vat

VAT_CATEGORY_VARIANT: dict[str, str] = {
    "zero": "success",
    "reduced": "info",
    "low": "info",
    "standard": "warning",
    "high": "warning",
    "premium": "danger",
}


def _vat_category(value) -> tuple[str, str]:
    if value == 0:
        return "zero", str(_("Tax Free"))
    elif value <= 5:
        return "reduced", str(_("Reduced Rate"))
    elif value <= 15:
        return "low", str(_("Low Rate"))
    elif value <= 25:
        return "standard", str(_("Standard Rate"))
    elif value <= 35:
        return "high", str(_("High Rate"))
    else:
        return "premium", str(_("Premium Rate"))


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
class VatAdmin(BaseModelAdmin):
    list_fullwidth = False

    list_display = [
        "vat_display",
        "vat_category",
        "usage_metrics",
        "calculation_preview",
        "created_at",
        "updated_at",
    ]

    list_filter = [
        VatUsageFilter,
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
        "products_using_vat",
    ]

    ordering = ["value"]

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
            _("Usage"),
            {
                "fields": ("products_using_vat",),
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

    @admin.display(description=_("VAT Rate"), ordering="value")
    def vat_display(self, obj):
        return f"{obj.value:g}%"

    @display(description=_("Category"), label=VAT_CATEGORY_VARIANT)
    def vat_category(self, obj):
        return _vat_category(obj.value)

    @admin.display(description=_("Usage"))
    def usage_metrics(self, obj):
        return _("%(count)d products") % {
            "count": getattr(obj, "products_count", 0)
        }

    @admin.display(description=_("Price Examples"))
    def calculation_preview(self, obj):
        examples = []
        for price in (10, 50, 100):
            vat_amount = (price * obj.value) / 100
            examples.append(f"€{price} → €{price + vat_amount:.2f}")
        return " | ".join(examples)

    @admin.display(description=_("Products Using This VAT"))
    def products_using_vat(self, obj):
        count = getattr(obj, "products_count", 0)
        if count == 0:
            return _("No products currently use this VAT rate")
        return _("%(count)d products use this VAT rate") % {"count": count}
