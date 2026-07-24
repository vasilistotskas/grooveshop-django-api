from datetime import timedelta

from django.contrib import admin, messages
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from admin.base import BaseTranslatableAdmin, BaseTranslatableTabularInline
from admin.displays import format_dt
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


class RegionInline(BaseTranslatableTabularInline):
    model = Region
    extra = 0
    fields = ("alpha", "name", "sort_order")
    ordering_field = "sort_order"
    hide_ordering_field = True
    tab = True
    show_change_link = True


@admin.register(Region)
class RegionAdmin(BaseTranslatableAdmin):
    list_display = (
        "region_info",
        "country_display",
        "region_stats",
        "sort_display",
        "completeness_badge",
        "created_display",
    )
    list_filter = [
        RegionStatusFilter,
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
            {"fields": ("region_analytics",), "classes": ("collapse",)},
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

    @display(description=_("Region"))
    def region_info(self, obj):
        name = obj.safe_translation_getter("name", any_language=True) or _(
            "Unnamed Region"
        )
        return f"{name} ({obj.alpha})"

    @display(description=_("Country"))
    def country_display(self, obj):
        name = obj.country.safe_translation_getter(
            "name", any_language=True
        ) or _("Unnamed Country")
        return f"{name} ({obj.country.alpha_2})"

    @display(description=_("Code Pattern"))
    def region_stats(self, obj):
        alpha = obj.alpha
        if alpha.isdigit():
            pattern = _("Numeric")
        elif alpha.isalpha():
            pattern = _("Alpha")
        else:
            pattern = _("Mixed")
        return _("%(len)d chars — %(pattern)s") % {
            "len": len(alpha),
            "pattern": pattern,
        }

    @display(description=_("Sort Order"), ordering="sort_order")
    def sort_display(self, obj):
        if obj.sort_order is not None:
            return obj.sort_order
        return str(_("No order"))

    @display(description=_("Completeness"))
    def completeness_badge(self, obj):
        total = 3
        done = sum(
            [
                bool(obj.alpha),
                bool(obj.safe_translation_getter("name", any_language=True)),
                bool(obj.country_id),
            ]
        )
        pct = done * 100 // total
        if pct == 100:
            label = _("Complete")
        elif pct >= 66:
            label = _("Good")
        elif pct >= 33:
            label = _("Partial")
        else:
            label = _("Poor")
        return f"{pct}% ({label})"

    @display(description=_("Created"), ordering="created_at")
    def created_display(self, obj):
        return format_dt(obj.created_at)

    @display(description=_("Region Analytics"))
    def region_analytics(self, obj):
        name = obj.safe_translation_getter("name", any_language=True) or ""
        return _(
            "Name length: %(name_len)d chars — Code length: %(code_len)d "
            "chars — Has name: %(has_name)s — Sort position: %(sort)s"
        ) % {
            "name_len": len(name),
            "code_len": len(obj.alpha),
            "has_name": _("Yes") if name else _("No"),
            "sort": obj.sort_order
            if obj.sort_order is not None
            else _("Not set"),
        }

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
