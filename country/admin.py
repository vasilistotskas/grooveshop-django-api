from django.contrib import admin, messages
from django.db import models
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from parler.admin import TranslatableAdmin
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
    RangeNumericFilter,
)
from unfold.decorators import action
from unfold.enums import ActionVariant

from country.models import Country


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
class CountryAdmin(ModelAdmin, TranslatableAdmin):
    list_fullwidth = True
    list_filter_submit = True

    list_display = [
        "country_info",
        "flag_display",
        "codes_display",
        "contact_info",
        "completeness_badge",
        "created_display",
    ]
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
    readonly_fields = (
        "uuid",
        "sort_order",
        "created_at",
        "updated_at",
    )
    list_per_page = 50
    ordering = ["sort_order", "alpha_2"]
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
            _("System Information"),
            {
                "fields": ("uuid", "sort_order", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def country_info(self, obj):
        name = (
            obj.safe_translation_getter("name", any_language=True)
            or "Unnamed Country"
        )
        has_iso = obj.iso_cc is not None
        status_icon = "✅" if has_iso else "⚠️"
        status_color = (
            "text-green-600 dark:text-green-400"
            if has_iso
            else "text-orange-600 dark:text-orange-400"
        )

        safe_status_color = conditional_escape(status_color)
        safe_status_icon = conditional_escape(status_icon)
        safe_name = conditional_escape(name)
        safe_alpha_2 = conditional_escape(obj.alpha_2)
        safe_sort_order = conditional_escape(str(obj.sort_order or "No order"))

        html = (
            f'<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100 flex items-center gap-2">'
            f'<span class="{safe_status_color}">{safe_status_icon}</span>'
            f"<span>{safe_name}</span>"
            f"</div>"
            f'<div class="text-base-600 dark:text-base-400">{safe_alpha_2}</div>'
            f'<div class="text-xs text-base-500 dark:text-base-400">Sort: {safe_sort_order}</div>'
            f"</div>"
        )
        return mark_safe(html)

    country_info.short_description = _("Country")

    def flag_display(self, obj):
        if obj.image_flag:
            safe_url = conditional_escape(obj.image_flag.url)
            html = (
                f'<div class="flex items-center justify-center">'
                f'<img src="{safe_url}" style="width: 48px; height: 32px; object-fit: cover; '
                f'border-radius: 4px; border: 1px solid #e5e7eb; box-shadow: 0 1px 3px rgba(0,0,0,0.1);" />'
                f"</div>"
            )
            return mark_safe(html)
        else:
            html = (
                '<div style="width: 48px; height: 32px; '
                "background: linear-gradient(45deg, #f3f4f6 25%, transparent 25%), "
                "linear-gradient(-45deg, #f3f4f6 25%, transparent 25%), "
                "linear-gradient(45deg, transparent 75%, #f3f4f6 75%), "
                "linear-gradient(-45deg, transparent 75%, #f3f4f6 75%); "
                "background-size: 8px 8px; background-position: 0 0, 0 4px, 4px -4px, -4px 0px; "
                "border-radius: 4px; border: 1px solid #e5e7eb; display: flex; align-items: center; "
                'justify-content: center; color: #9ca3af; font-size: 12px;">'
                "🏳️"
                "</div>"
            )
            return mark_safe(html)

    flag_display.short_description = _("Flag")

    def codes_display(self, obj):
        safe_a2 = conditional_escape(obj.alpha_2)
        safe_a3 = conditional_escape(obj.alpha_3)
        alpha_2_badge = (
            f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            f"bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200 rounded border "
            f'border-blue-200 dark:border-blue-700">{safe_a2}</span>'
        )
        alpha_3_badge = (
            f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            f"bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 rounded border "
            f'border-green-200 dark:border-green-700">{safe_a3}</span>'
        )

        iso_display = str(obj.iso_cc) if obj.iso_cc else "—"
        safe_iso_display = conditional_escape(iso_display)
        iso_color = (
            "text-base-900 dark:text-base-100"
            if obj.iso_cc
            else "text-base-400 dark:text-base-500"
        )
        safe_iso_color = conditional_escape(iso_color)

        html = (
            '<div class="text-sm space-y-1">'
            f'<div class="flex gap-1">{alpha_2_badge} {alpha_3_badge}</div>'
            f'<div class="{safe_iso_color}">ISO: {safe_iso_display}</div>'
            "</div>"
        )
        return mark_safe(html)

    codes_display.short_description = _("Codes")

    def contact_info(self, obj):
        if obj.phone_code:
            code_str = str(obj.phone_code)
            if len(code_str) == 1:
                badge_class = "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 border-green-200 dark:border-green-700"
            elif len(code_str) == 2:
                badge_class = "bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200 border-blue-200 dark:border-blue-700"
            elif len(code_str) == 3:
                badge_class = "bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-200 border-orange-200 dark:border-orange-700"
            else:
                badge_class = "bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200 border-red-200 dark:border-red-700"

            safe_badge_class = conditional_escape(badge_class)
            safe_code = conditional_escape(code_str)
            phone_badge = (
                f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                f'{safe_badge_class} rounded border">📞 +{safe_code}</span>'
            )
        else:
            phone_badge = (
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                "bg-gray-50 dark:bg-gray-900 text-base-500 dark:text-base-400 rounded border "
                'border-gray-200 dark:border-gray-700">📞 No Code</span>'
            )

        html = f'<div class="text-sm"><div>{phone_badge}</div></div>'
        return mark_safe(html)

    contact_info.short_description = _("Contact")

    def completeness_badge(self, obj):
        total_fields = 4
        completed_fields = 0

        if obj.safe_translation_getter("name", any_language=True):
            completed_fields += 1
        if obj.iso_cc is not None:
            completed_fields += 1
        if obj.phone_code is not None:
            completed_fields += 1
        if obj.image_flag:
            completed_fields += 1

        percentage = (completed_fields / total_fields) * 100

        if percentage == 100:
            badge_class = "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 border-green-200 dark:border-green-700"
            icon = "✅"
            label = "Complete"
        elif percentage >= 75:
            badge_class = "bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200 border-blue-200 dark:border-blue-700"
            icon = "🔷"
            label = "Good"
        elif percentage >= 50:
            badge_class = "bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-200 border-yellow-200 dark:border-yellow-700"
            icon = "⚠️"
            label = "Partial"
        else:
            badge_class = "bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200 border-red-200 dark:border-red-700"
            icon = "❌"
            label = "Incomplete"

        safe_percentage = conditional_escape(str(int(percentage)))
        safe_badge_class = conditional_escape(badge_class)
        safe_icon = conditional_escape(icon)
        safe_label = conditional_escape(label)

        html = (
            f'<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_percentage}%</div>'
            f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium {safe_badge_class} rounded border gap-1">'
            f"<span>{safe_icon}</span>"
            f"<span>{safe_label}</span>"
            f"</span>"
            f"</div>"
        )
        return mark_safe(html)

    completeness_badge.short_description = _("Completeness")

    def created_display(self, obj):
        date_str = obj.created_at.strftime("%Y-%m-%d")
        time_str = obj.created_at.strftime("%H:%M")
        safe_date = conditional_escape(date_str)
        safe_time = conditional_escape(time_str)

        html = (
            f'<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_date}</div>'
            f'<div class="text-base-600 dark:text-base-400">{safe_time}</div>'
            f"</div>"
        )
        return mark_safe(html)

    created_display.short_description = _("Created")

    @action(
        description=_("Update sort order"),
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
