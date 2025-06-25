from datetime import timedelta

from django.contrib import admin, messages
from django.db import models
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from parler.admin import TranslatableAdmin, TranslatableTabularInline
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)
from unfold.decorators import action
from unfold.enums import ActionVariant

from region.models import Region


class RegionStatusFilter(DropdownFilter):
    title = _("Region Status")
    parameter_name = "region_status"

    def lookups(self, request, model_admin):
        return [
            ("has_name", _("Has Name")),
            ("no_name", _("No Name")),
            ("recent", _("Recently Added")),
            ("by_continent", _("Group by Continent")),
        ]

    def queryset(self, request, queryset):
        if self.value() == "has_name":
            return queryset.exclude(translations__name__isnull=True).exclude(
                translations__name=""
            )
        elif self.value() == "no_name":
            return queryset.filter(
                models.Q(translations__name__isnull=True)
                | models.Q(translations__name="")
            )
        elif self.value() == "recent":
            thirty_days_ago = timezone.now() - timedelta(days=30)
            return queryset.filter(created_at__gte=thirty_days_ago)
        return queryset


class CountryGroupFilter(DropdownFilter):
    title = _("Country Groups")
    parameter_name = "country_group"

    def lookups(self, request, model_admin):
        return [
            ("europe", _("European Countries")),
            ("asia", _("Asian Countries")),
            ("america", _("American Countries")),
            ("africa", _("African Countries")),
            ("oceania", _("Oceania Countries")),
        ]

    def queryset(self, request, queryset):
        continent_mappings = {
            "europe": [
                "AD",
                "AT",
                "BE",
                "CH",
                "DE",
                "DK",
                "ES",
                "FI",
                "FR",
                "GB",
                "IE",
                "IS",
                "IT",
                "LI",
                "LU",
                "MC",
                "NL",
                "NO",
                "PT",
                "SE",
                "SM",
                "VA",
                "AL",
                "BA",
                "BG",
                "BY",
                "CZ",
                "EE",
                "HR",
                "HU",
                "LT",
                "LV",
                "MD",
                "ME",
                "MK",
                "PL",
                "RO",
                "RS",
                "SI",
                "SK",
                "UA",
                "CY",
                "GR",
                "MT",
                "AX",
                "FO",
                "GG",
                "IM",
                "JE",
                "SJ",
            ],
            "asia": [
                "CN",
                "HK",
                "JP",
                "KP",
                "KR",
                "MN",
                "MO",
                "TW",
                "BN",
                "ID",
                "KH",
                "LA",
                "MM",
                "MY",
                "PH",
                "SG",
                "TH",
                "TL",
                "VN",
                "AF",
                "BD",
                "BT",
                "IN",
                "LK",
                "MV",
                "NP",
                "PK",
                "KG",
                "KZ",
                "TJ",
                "TM",
                "UZ",
                "AE",
                "AM",
                "AZ",
                "BH",
                "CY",
                "GE",
                "IL",
                "IQ",
                "IR",
                "JO",
                "KW",
                "LB",
                "OM",
                "PS",
                "QA",
                "SA",
                "SY",
                "TR",
                "YE",
            ],
            "america": [
                "CA",
                "GL",
                "MX",
                "PM",
                "US",
                "BZ",
                "CR",
                "GT",
                "HN",
                "NI",
                "PA",
                "SV",
                "AG",
                "AI",
                "AW",
                "BB",
                "BL",
                "BM",
                "BQ",
                "BS",
                "CU",
                "CW",
                "DM",
                "DO",
                "GD",
                "GP",
                "HT",
                "JM",
                "KN",
                "KY",
                "LC",
                "MF",
                "MQ",
                "MS",
                "PR",
                "SX",
                "TC",
                "TT",
                "VG",
                "VI",
                "AR",
                "BO",
                "BR",
                "CL",
                "CO",
                "EC",
                "FK",
                "GF",
                "GY",
                "PE",
                "PY",
                "SR",
                "UY",
                "VE",
            ],
            "africa": [
                "DZ",
                "EG",
                "EH",
                "LY",
                "MA",
                "SD",
                "SS",
                "TN",
                "BF",
                "BJ",
                "CI",
                "CV",
                "GH",
                "GM",
                "GN",
                "GW",
                "LR",
                "ML",
                "MR",
                "NE",
                "NG",
                "SH",
                "SL",
                "SN",
                "TG",
                "AO",
                "CD",
                "CF",
                "CG",
                "CM",
                "GA",
                "GQ",
                "ST",
                "TD",
                "BI",
                "DJ",
                "ER",
                "ET",
                "KE",
                "KM",
                "MG",
                "MU",
                "MW",
                "MZ",
                "RE",
                "RW",
                "SC",
                "SO",
                "TZ",
                "UG",
                "YT",
                "ZM",
                "ZW",
                "BW",
                "LS",
                "NA",
                "SZ",
                "ZA",
            ],
            "oceania": [
                "AU",
                "CX",
                "HM",
                "NF",
                "NZ",
                "FJ",
                "NC",
                "PG",
                "SB",
                "VU",
                "FM",
                "GU",
                "KI",
                "MH",
                "MP",
                "NR",
                "PW",
                "AS",
                "CK",
                "NU",
                "PF",
                "PN",
                "TO",
                "TV",
                "WF",
                "WS",
            ],
        }

        if self.value() in continent_mappings:
            country_codes = continent_mappings[self.value()]
            return queryset.filter(country__alpha_2__in=country_codes)

        return queryset


class RegionInline(TranslatableTabularInline):
    model = Region
    extra = 0
    fields = ("alpha", "name", "sort_order")
    readonly_fields = ("sort_order",)

    tab = True
    show_change_link = True


@admin.register(Region)
class RegionAdmin(ModelAdmin, TranslatableAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    list_display = [
        "region_info",
        "country_display",
        "region_stats",
        "sort_display",
        "completeness_badge",
        "created_display",
    ]
    list_filter = [
        RegionStatusFilter,
        CountryGroupFilter,
        ("country", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
    ]
    search_fields = [
        "alpha",
        "translations__name",
        "country__alpha_2",
        "country__alpha_3",
        "country__translations__name",
    ]
    readonly_fields = (
        "uuid",
        "sort_order",
        "created_at",
        "updated_at",
        "region_analytics",
        "country_analytics",
    )
    list_select_related = ["country"]
    list_per_page = 50
    ordering = ["country__alpha_2", "sort_order", "alpha"]
    actions = [
        "update_sort_order",
    ]

    fieldsets = (
        (
            _("Region Information"),
            {
                "fields": ("alpha", "name", "country"),
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
            _("Analytics"),
            {
                "fields": ("region_analytics", "country_analytics"),
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
        return (
            super()
            .get_queryset(request)
            .select_related("country")
            .prefetch_related("country__translations")
        )

    def region_info(self, obj):
        name = (
            obj.safe_translation_getter("name", any_language=True)
            or "Unnamed Region"
        )
        alpha = obj.alpha

        has_name = bool(obj.safe_translation_getter("name", any_language=True))
        status_icon = "✅" if has_name else "⚠️"
        status_color = (
            "text-green-600 dark:text-green-400"
            if has_name
            else "text-orange-600 dark:text-orange-400"
        )

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100 flex items-center gap-2">'
            '<span class="{}">{}</span>'
            "<span>{}</span>"
            "</div>"
            '<div class="text-base-600 dark:text-base-400">Code: {}</div>'
            '<div class="text-xs text-base-500 dark:text-base-400">Sort: {}</div>'
            "</div>",
            status_color,
            status_icon,
            name,
            alpha,
            obj.sort_order or "No order",
        )

    region_info.short_description = _("Region")

    def country_display(self, obj):
        country = obj.country
        country_name = (
            country.safe_translation_getter("name", any_language=True)
            or "Unnamed Country"
        )

        flag_html = ""
        if country.image_flag:
            flag_html = format_html(
                '<img src="{}" style="width: 24px; height: 16px; object-fit: cover; border-radius: 2px; border: 1px solid #e5e7eb; margin-right: 8px;" />',
                country.image_flag.url,
            )
        else:
            flag_html = format_html(
                '<div style="width: 24px; height: 16px; background: #f3f4f6; border-radius: 2px; border: 1px solid #e5e7eb; margin-right: 8px; display: flex; align-items: center; justify-content: center; font-size: 8px;">🏳️</div>'
            )

        codes_badge = format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200 rounded border">'
            "{}"
            "</span>",
            country.alpha_2,
        )

        return format_html(
            '<div class="text-sm">'
            '<div class="flex items-center">'
            "{}"
            "<div>"
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="flex gap-1 mt-1">{}</div>'
            "</div>"
            "</div>"
            "</div>",
            flag_html,
            country_name,
            codes_badge,
        )

    country_display.short_description = _("Country")

    def region_stats(self, obj):
        name_length = len(
            obj.safe_translation_getter("name", any_language=True) or ""
        )
        code_length = len(obj.alpha)

        is_numeric = obj.alpha.isdigit()
        is_alpha = obj.alpha.isalpha()

        if is_numeric:
            pattern_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 rounded">'
                "🔢 Numeric"
                "</span>"
            )
        elif is_alpha:
            pattern_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200 rounded">'
                "🔤 Alpha"
                "</span>"
            )
        else:
            pattern_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-200 rounded">'
                "🔀 Mixed"
                "</span>"
            )

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{} chars</div>'
            '<div class="text-base-600 dark:text-base-400">{} name</div>'
            '<div class="mt-1">{}</div>'
            "</div>",
            code_length,
            f"{name_length} char" if name_length > 0 else "No",
            pattern_badge,
        )

    region_stats.short_description = _("Stats")

    def sort_display(self, obj):
        sort_order = obj.sort_order

        if sort_order is None:
            badge_class = (
                "bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200"
            )
            icon = "❌"
            label = "No Order"
        elif sort_order == 0:
            badge_class = "bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-200"
            icon = "⚠️"
            label = "First"
        else:
            badge_class = "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200"
            icon = "✅"
            label = f"#{sort_order}"

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium {} rounded gap-1">'
            "<span>{}</span>"
            "<span>{}</span>"
            "</span>"
            "</div>",
            sort_order if sort_order is not None else "None",
            badge_class,
            icon,
            label,
        )

    sort_display.short_description = _("Sort Order")

    def completeness_badge(self, obj):
        total_fields = 3
        completed_fields = 0

        if obj.alpha:
            completed_fields += 1
        if obj.safe_translation_getter("name", any_language=True):
            completed_fields += 1
        if obj.country:
            completed_fields += 1

        percentage = (completed_fields / total_fields) * 100

        if percentage == 100:
            badge_class = "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200"
            icon = "✅"
            label = "Complete"
        elif percentage >= 66:
            badge_class = (
                "bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200"
            )
            icon = "🔷"
            label = "Good"
        elif percentage >= 33:
            badge_class = "bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-200"
            icon = "⚠️"
            label = "Partial"
        else:
            badge_class = (
                "bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200"
            )
            icon = "❌"
            label = "Poor"

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}%</div>'
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium {} rounded border gap-1">'
            "<span>{}</span>"
            "<span>{}</span>"
            "</span>"
            "</div>",
            int(percentage),
            badge_class,
            icon,
            label,
        )

    completeness_badge.short_description = _("Completeness")

    def created_display(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="text-base-600 dark:text-base-400">{}</div>'
            "</div>",
            obj.created_at.strftime("%Y-%m-%d"),
            obj.created_at.strftime("%H:%M"),
        )

    created_display.short_description = _("Created")

    def region_analytics(self, obj):
        name = obj.safe_translation_getter("name", any_language=True) or ""
        name_length = len(name)
        code_length = len(obj.alpha)

        is_numeric = obj.alpha.isdigit()
        is_alpha = obj.alpha.isalpha()
        has_uppercase = any(c.isupper() for c in obj.alpha)
        has_lowercase = any(c.islower() for c in obj.alpha)

        code_pattern = (
            "Numeric" if is_numeric else "Alpha" if is_alpha else "Mixed"
        )
        case_pattern = (
            "Mixed"
            if has_uppercase and has_lowercase
            else "Upper"
            if has_uppercase
            else "Lower"
        )

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Name Length:</strong></div><div>{} chars</div>"
            "<div><strong>Code Length:</strong></div><div>{} chars</div>"
            "<div><strong>Code Pattern:</strong></div><div>{}</div>"
            "<div><strong>Case Pattern:</strong></div><div>{}</div>"
            "<div><strong>Has Name:</strong></div><div>{}</div>"
            "<div><strong>Sort Position:</strong></div><div>{}</div>"
            "</div>"
            "</div>",
            name_length,
            code_length,
            code_pattern,
            case_pattern,
            "Yes" if name else "No",
            obj.sort_order if obj.sort_order is not None else "Not Set",
        )

    region_analytics.short_description = _("Region Analytics")

    def country_analytics(self, obj):
        country = obj.country
        country_name = (
            country.safe_translation_getter("name", any_language=True)
            or "Unknown"
        )

        total_regions = Region.objects.filter(country=country).count()
        current_position = (
            Region.objects.filter(
                country=country, sort_order__lt=obj.sort_order or 9999
            ).count()
            + 1
        )

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Country:</strong></div><div>{}</div>"
            "<div><strong>Country Code:</strong></div><div>{}</div>"
            "<div><strong>Total Regions:</strong></div><div>{}</div>"
            "<div><strong>Position:</strong></div><div>{} of {}</div>"
            "<div><strong>Has Flag:</strong></div><div>{}</div>"
            "<div><strong>Has ISO:</strong></div><div>{}</div>"
            "</div>"
            "</div>",
            country_name,
            country.alpha_2,
            total_regions,
            current_position,
            total_regions,
            "Yes" if country.image_flag else "No",
            "Yes" if country.iso_cc else "No",
        )

    country_analytics.short_description = _("Country Analytics")

    @action(
        description=_("Update sort order"),
        variant=ActionVariant.INFO,
        icon="sort",
    )
    def update_sort_order(self, request, queryset):
        countries = set(queryset.values_list("country", flat=True))
        updated = 0

        for country_id in countries:
            regions = list(
                Region.objects.filter(country_id=country_id).order_by("alpha")
            )
            for index, region in enumerate(regions):
                region.sort_order = index
                region.save(update_fields=["sort_order"])
                updated += 1

        self.message_user(
            request,
            _("Updated sort order for %(count)d regions.") % {"count": updated},
            messages.SUCCESS,
        )
