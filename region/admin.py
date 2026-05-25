from datetime import timedelta
from django.contrib import admin, messages
from django.db import models
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe
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
        value = self.value()
        codes = continent_mappings.get(value) if value else None
        if codes:
            return queryset.filter(country__alpha_2__in=codes)
        return queryset


class RegionInline(TranslatableTabularInline):
    model = Region
    extra = 0
    fields = ("alpha", "name", "sort_order")
    ordering_field = "sort_order"
    hide_ordering_field = True
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
    ordering_field = "sort_order"
    hide_ordering_field = True
    readonly_fields = (
        "uuid",
        "created_at",
        "updated_at",
        "region_analytics",
        "country_analytics",
    )
    list_select_related = ["country"]
    list_per_page = 50
    ordering = ["country__alpha_2", "sort_order", "alpha"]
    actions = ["update_sort_order"]

    fieldsets = (
        (
            _("Region Information"),
            {"fields": ("alpha", "name", "country"), "classes": ("wide",)},
        ),
        (
            _("Organization"),
            {"fields": ("sort_order",), "classes": ("wide",)},
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

    @admin.display(description=_("Region"))
    def region_info(self, obj):
        name = (
            obj.safe_translation_getter("name", any_language=True)
            or "Unnamed Region"
        )
        alpha = obj.alpha
        sort = obj.sort_order if obj.sort_order is not None else "No order"

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
            '<span class="{color}">{icon}</span>'
            "<span>{name}</span>"
            "</div>"
            '<div class="text-base-600 dark:text-base-400">Code: {alpha}</div>'
            '<div class="text-xs text-base-600 dark:text-base-300">Sort: {sort}</div>'
            "</div>",
            color=status_color,
            icon=status_icon,
            name=name,
            alpha=alpha,
            sort=str(sort),
        )

    @admin.display(description=_("Country"))
    def country_display(self, obj):
        country = obj.country
        country_name = (
            country.safe_translation_getter("name", any_language=True)
            or "Unnamed Country"
        )

        if country.image_flag:
            flag_html = format_html(
                '<img src="{url}" '
                'style="width:24px;height:16px;object-fit:cover;border-radius:2px;'
                'border:1px solid #e5e7eb;margin-right:8px;" />',
                url=country.image_flag.url,
            )
        else:
            flag_html = mark_safe(
                '<div style="width:24px;height:16px;background:#f3f4f6;border-radius:2px;'
                "border:1px solid #e5e7eb;margin-right:8px;display:flex;align-items:center;"
                'justify-content:center;font-size:8px;">🏳️</div>'
            )

        return format_html(
            '<div class="text-sm">'
            '<div class="flex items-center">'
            "{flag}"
            "<div>"
            '<div class="font-medium text-base-900 dark:text-base-100">{country}</div>'
            '<div class="flex gap-1 mt-1">'
            '<span class="inline-flex items-center px-2 py-1 text-xs'
            " font-medium bg-blue-50 dark:bg-blue-900 text-blue-700"
            ' dark:text-blue-200 rounded border">{code}</span>'
            "</div>"
            "</div>"
            "</div>"
            "</div>",
            flag=flag_html,
            country=country_name,
            code=country.alpha_2,
        )

    @admin.display(description=_("Stats"))
    def region_stats(self, obj):
        name_len = len(
            obj.safe_translation_getter("name", any_language=True) or ""
        )
        code_len = len(obj.alpha)
        is_num = obj.alpha.isdigit()
        is_alpha = obj.alpha.isalpha()

        if is_num:
            badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 rounded">🔢 Numeric</span>'
            )
        elif is_alpha:
            badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200 rounded">🔤 Alpha</span>'
            )
        else:
            badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-200 rounded">🔀 Mixed</span>'
            )

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{code_len} chars</div>'
            '<div class="text-base-600 dark:text-base-400">{name_len} name</div>'
            '<div class="mt-1">{badge}</div>'
            "</div>",
            code_len=code_len,
            name_len=name_len,
            badge=badge,
        )

    @admin.display(description=_("Sort Order"))
    def sort_display(self, obj):
        sort = obj.sort_order
        if sort is None:
            badge_class = (
                "bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200"
            )
            icon = "❌"
            label = "No Order"
        elif sort == 0:
            badge_class = "bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-200"
            icon = "⚠️"
            label = "First"
        else:
            badge_class = "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200"
            icon = "✅"
            label = f"#{sort}"

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{sort}</div>'
            '<span class="inline-flex items-center px-2 py-1 text-xs'
            ' font-medium {badge_class} rounded gap-1">'
            "<span>{icon}</span><span>{label}</span>"
            "</span>"
            "</div>",
            sort=str(sort if sort is not None else "None"),
            badge_class=badge_class,
            icon=icon,
            label=label,
        )

    @admin.display(description=_("Completeness"))
    def completeness_badge(self, obj):
        total = 3
        done = 0
        if obj.alpha:
            done += 1
        if obj.safe_translation_getter("name", any_language=True):
            done += 1
        if obj.country:
            done += 1
        pct = done * 100 // total

        if pct == 100:
            cls = "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200"
            icon = "✅"
            lbl = "Complete"
        elif pct >= 66:
            cls = "bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200"
            icon = "🔷"
            lbl = "Good"
        elif pct >= 33:
            cls = "bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-200"
            icon = "⚠️"
            lbl = "Partial"
        else:
            cls = "bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200"
            icon = "❌"
            lbl = "Poor"

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{pct}%</div>'
            '<span class="inline-flex items-center px-2 py-1 text-xs'
            ' font-medium {cls} rounded border gap-1">'
            "<span>{icon}</span><span>{lbl}</span>"
            "</span>"
            "</div>",
            pct=pct,
            cls=cls,
            icon=icon,
            lbl=lbl,
        )

    @admin.display(description=_("Created"))
    def created_display(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{date}</div>'
            '<div class="text-base-600 dark:text-base-400">{time}</div>'
            "</div>",
            date=obj.created_at.strftime("%Y-%m-%d"),
            time=obj.created_at.strftime("%H:%M"),
        )

    @admin.display(description=_("Region Analytics"))
    def region_analytics(self, obj):
        name = obj.safe_translation_getter("name", any_language=True) or ""
        name_len = len(name)
        code_len = len(obj.alpha)
        is_num = obj.alpha.isdigit()
        is_alpha = obj.alpha.isalpha()
        has_up = any(c.isupper() for c in obj.alpha)
        has_lo = any(c.islower() for c in obj.alpha)

        pattern = "Numeric" if is_num else "Alpha" if is_alpha else "Mixed"
        case = "Mixed" if has_up and has_lo else "Upper" if has_up else "Lower"

        return format_html(
            '<div class="text-sm"><div class="grid grid-cols-2 gap-2">'
            "<div><strong>Name Length:</strong></div><div>{name_len} chars</div>"
            "<div><strong>Code Length:</strong></div><div>{code_len} chars</div>"
            "<div><strong>Code Pattern:</strong></div><div>{pattern}</div>"
            "<div><strong>Case Pattern:</strong></div><div>{case}</div>"
            "<div><strong>Has Name:</strong></div><div>{has_name}</div>"
            "<div><strong>Sort Position:</strong></div><div>{sort}</div>"
            "</div></div>",
            name_len=name_len,
            code_len=code_len,
            pattern=pattern,
            case=case,
            has_name="Yes" if name else "No",
            sort=str(obj.sort_order)
            if obj.sort_order is not None
            else "Not Set",
        )

    @admin.display(description=_("Country Analytics"))
    def country_analytics(self, obj):
        country = obj.country
        cname = (
            country.safe_translation_getter("name", any_language=True)
            or "Unknown"
        )
        total_regions = Region.objects.filter(country=country).count()
        position = (
            Region.objects.filter(
                country=country, sort_order__lt=(obj.sort_order or 9999)
            ).count()
            + 1
        )

        return format_html(
            '<div class="text-sm"><div class="grid grid-cols-2 gap-2">'
            "<div><strong>Country:</strong></div><div>{cname}</div>"
            "<div><strong>Country Code:</strong></div><div>{code}</div>"
            "<div><strong>Total Regions:</strong></div><div>{total}</div>"
            "<div><strong>Position:</strong></div><div>{pos} of {total}</div>"
            "<div><strong>Has Flag:</strong></div><div>{has_flag}</div>"
            "<div><strong>Has ISO:</strong></div><div>{has_iso}</div>"
            "</div></div>",
            cname=cname,
            code=country.alpha_2,
            total=total_regions,
            pos=position,
            has_flag="Yes" if country.image_flag else "No",
            has_iso="Yes" if country.iso_cc else "No",
        )

    @action(
        description=str(_("Update sort order")),
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
