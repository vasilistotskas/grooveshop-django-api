from django.contrib import admin, messages
from django.db import models
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
    RangeNumericFilter,
)
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from admin.base import BaseTranslatableAdmin
from admin.displays import format_dt
from country.models import Country
from region.admin import RegionInline


class CountryStatusFilter(DropdownFilter):
    title = _("Country Status")
    parameter_name = "country_status"

    def lookups(self, request, model_admin):
        return [
            ("active", _("Active (Has ISO Code)")),
            ("incomplete", _("Incomplete (Missing Data)")),
            ("with_phone", _("Has Phone Code")),
            ("with_flag", _("Has Flag Image")),
            ("complete", _("Complete Profile")),
        ]

    def queryset(self, request, queryset):
        if self.value() == "active":
            return queryset.filter(iso_cc__isnull=False)
        elif self.value() == "incomplete":
            return queryset.filter(
                models.Q(iso_cc__isnull=True)
                | models.Q(phone_code__isnull=True)
                | models.Q(image_flag__isnull=True)
            )
        elif self.value() == "with_phone":
            return queryset.filter(phone_code__isnull=False)
        elif self.value() == "with_flag":
            return queryset.filter(image_flag__isnull=False)
        elif self.value() == "complete":
            return queryset.filter(
                iso_cc__isnull=False,
                phone_code__isnull=False,
                image_flag__isnull=False,
            )
        return queryset


@admin.register(Country)
class CountryAdmin(BaseTranslatableAdmin):
    list_display = (
        "country_info",
        "flag_display",
        "codes_display",
        "contact_info",
        "completeness_badge",
        "created_display",
    )
    list_filter = [
        CountryStatusFilter,
        ("iso_cc", RangeNumericFilter),
        ("phone_code", RangeNumericFilter),
        ("created_at", RangeDateTimeFilter),
    ]
    search_fields = [
        "translations__name",
        "alpha_2",
        "alpha_3",
        "iso_cc",
        "phone_code",
    ]
    ordering_field = "sort_order"
    hide_ordering_field = True
    readonly_fields = (
        "uuid",
        "created_at",
        "updated_at",
    )
    list_per_page = 50
    ordering = ["sort_order", "alpha_2"]
    inlines = [RegionInline]
    actions = [
        "update_sort_order",
    ]

    fieldsets = (
        (
            _("Country Information"),
            {
                "fields": ("name",),
                "classes": ("wide",),
            },
        ),
        (
            _("Country Codes"),
            {
                "fields": ("alpha_2", "alpha_3", "iso_cc"),
                "classes": ("wide",),
            },
        ),
        (
            _("Contact & Media"),
            {
                "fields": ("phone_code", "image_flag"),
                "classes": ("wide",),
            },
        ),
        (
            _("Organization"),
            {
                "fields": ("sort_order",),
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

    @display(description=_("Country"), ordering="alpha_2")
    def country_info(self, obj):
        name = obj.safe_translation_getter("name", any_language=True) or _(
            "Unnamed Country"
        )
        return f"{name} ({obj.alpha_2})"

    @admin.display(description=_("Flag"), empty_value="—")
    def flag_display(self, obj):
        if not obj.image_flag:
            return None
        return format_html(
            '<img src="{url}" width="32" height="22" alt="" />',
            url=obj.image_flag.url,
        )

    @display(description=_("Codes"))
    def codes_display(self, obj):
        iso = obj.iso_cc if obj.iso_cc is not None else "—"
        return f"{obj.alpha_2} / {obj.alpha_3} — ISO {iso}"

    @display(description=_("Contact"))
    def contact_info(self, obj):
        if obj.phone_code is None:
            return str(_("No phone code"))
        return f"+{obj.phone_code}"

    @display(description=_("Completeness"))
    def completeness_badge(self, obj):
        total = 4
        done = sum(
            [
                bool(obj.safe_translation_getter("name", any_language=True)),
                obj.iso_cc is not None,
                obj.phone_code is not None,
                bool(obj.image_flag),
            ]
        )
        pct = done * 100 // total
        if pct == 100:
            label = _("Complete")
        elif pct >= 75:
            label = _("Good")
        elif pct >= 50:
            label = _("Partial")
        else:
            label = _("Incomplete")
        return f"{pct}% ({label})"

    @display(description=_("Created"), ordering="created_at")
    def created_display(self, obj):
        return format_dt(obj.created_at)

    @action(
        description=str(_("Update sort order")),
        variant=ActionVariant.INFO,
        icon="sort",
    )
    def update_sort_order(self, request, queryset):
        countries = list(queryset.order_by("alpha_2"))
        for index, country in enumerate(countries):
            country.sort_order = index
            country.save(update_fields=["sort_order"])

        count = len(countries)
        self.message_user(
            request,
            _("Updated sort order for %(count)d countries.") % {"count": count},
            messages.SUCCESS,
        )
