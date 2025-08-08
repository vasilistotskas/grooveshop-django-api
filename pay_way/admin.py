from django.contrib import admin
from django.db import models
from django.db.models import Q
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from parler.admin import TranslatableAdmin
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
    RangeNumericListFilter,
)
from unfold.contrib.forms.widgets import WysiwygWidget
from unfold.decorators import action
from unfold.enums import ActionVariant

from pay_way.models import PayWay


class CostRangeFilter(RangeNumericListFilter):
    title = _("Cost Range")
    parameter_name = "cost_range"

    def queryset(self, request, queryset):
        filters = {}
        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["cost__gte"] = value_from
        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["cost__lte"] = value_to
        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class FreeThresholdFilter(RangeNumericListFilter):
    title = _("Free Threshold Range")
    parameter_name = "free_threshold_range"

    def queryset(self, request, queryset):
        filters = {}
        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["free_threshold__gte"] = value_from
        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["free_threshold__lte"] = value_to
        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class PaymentTypeFilter(DropdownFilter):
    title = _("Payment Type")
    parameter_name = "payment_type"

    def lookups(self, request, model_admin):
        return [
            ("online", _("Online Payment")),
            ("offline_simple", _("Offline (Simple)")),
            ("offline_confirmation", _("Offline (Requires Confirmation)")),
        ]

    def queryset(self, request, queryset):
        if self.value() == "online":
            return queryset.filter(is_online_payment=True)
        elif self.value() == "offline_simple":
            return queryset.filter(
                is_online_payment=False, requires_confirmation=False
            )
        elif self.value() == "offline_confirmation":
            return queryset.filter(
                is_online_payment=False, requires_confirmation=True
            )
        return queryset


class ConfigurationStatusFilter(DropdownFilter):
    title = _("Configuration Status")
    parameter_name = "configuration_status"

    def lookups(self, request, model_admin):
        return [
            ("configured", _("Configured")),
            ("not_configured", _("Not Configured")),
            ("no_config_needed", _("No Configuration Needed")),
        ]

    def queryset(self, request, queryset):
        if self.value() == "configured":
            return queryset.filter(
                is_online_payment=True, configuration__isnull=False
            ).exclude(configuration={})
        elif self.value() == "not_configured":
            return queryset.filter(is_online_payment=True).filter(
                Q(configuration__isnull=True) | Q(configuration={})
            )
        elif self.value() == "no_config_needed":
            return queryset.filter(is_online_payment=False)
        return queryset


@admin.register(PayWay)
class PayWayAdmin(ModelAdmin, TranslatableAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    formfield_overrides = {
        models.TextField: {"widget": WysiwygWidget},
    }

    list_display = [
        "name_display",
        "provider_code_badge",
        "active_status",
        "active",
        "payment_type_display",
        "cost_display",
        "free_threshold_display",
        "configuration_status",
        "icon_preview",
        "sort_order_display",
    ]

    list_filter = [
        "active",
        PaymentTypeFilter,
        ConfigurationStatusFilter,
        "is_online_payment",
        "requires_confirmation",
        CostRangeFilter,
        FreeThresholdFilter,
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
    ]

    list_editable = ["active"]

    search_fields = [
        "translations__name",
        "provider_code",
        "translations__description",
        "translations__instructions",
    ]

    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "configuration_preview",
        "effective_cost_display",
        "is_configured_status",
        "sort_order",
    ]

    ordering = ["sort_order", "id"]

    actions = [
        "activate_payment_methods",
        "deactivate_payment_methods",
        "move_up_in_order",
        "move_down_in_order",
        "reset_sort_order",
    ]

    fieldsets = (
        (
            _("Basic Information"),
            {"fields": ("active", "sort_order"), "classes": ("wide",)},
        ),
        (
            _("Display & Branding"),
            {"fields": ("name", "icon"), "classes": ("wide",)},
        ),
        (
            _("Content"),
            {"fields": ("description", "instructions"), "classes": ("wide",)},
        ),
        (
            _("Payment Configuration"),
            {
                "fields": (
                    "provider_code",
                    "is_online_payment",
                    "requires_confirmation",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Pricing"),
            {
                "fields": ("cost", "free_threshold", "effective_cost_display"),
                "classes": ("wide",),
            },
        ),
        (
            _("Advanced Configuration"),
            {
                "fields": (
                    "configuration",
                    "configuration_preview",
                    "is_configured_status",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": ("id", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def name_display(self, obj):
        name = (
            obj.safe_translation_getter("name", any_language=True)
            or "Unnamed Payment Method"
        )
        safe_name = conditional_escape(name)
        html = f'<strong class="text-base-900 dark:text-base-100">{safe_name}</strong>'
        return mark_safe(html)

    name_display.short_description = _("Name")

    def provider_code_badge(self, obj):
        if obj.provider_code:
            safe_code = conditional_escape(obj.provider_code)
            html = (
                f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                f'bg-indigo-50 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-300 rounded-full">'
                f"{safe_code}"
                f"</span>"
            )
            return mark_safe(html)
        return mark_safe(
            '<span class="text-base-400 dark:text-base-500 italic">'
            "No provider"
            "</span>"
        )

    provider_code_badge.short_description = _("Provider")

    def active_status(self, obj):
        if obj.active:
            return mark_safe(
                '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full gap-1">'
                "<span>✓</span><span>Active</span>"
                "</span>"
            )
        return mark_safe(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full gap-1">'
            "<span>✗</span><span>Inactive</span>"
            "</span>"
        )

    active_status.short_description = _("Status")

    def payment_type_display(self, obj):
        if obj.is_online_payment:
            return mark_safe(
                '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
                'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full gap-1">'
                "<span>🌐</span><span>Online</span>"
                "</span>"
            )
        elif obj.requires_confirmation:
            return mark_safe(
                '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
                'bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded-full gap-1">'
                "<span>⏱️</span><span>Offline (Confirm)</span>"
                "</span>"
            )
        return mark_safe(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            'bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-300 rounded-full gap-1">'
            "<span>📝</span><span>Offline</span>"
            "</span>"
        )

    payment_type_display.short_description = _("Type")

    def cost_display(self, obj):
        if obj.cost and obj.cost.amount > 0:
            safe_amount = conditional_escape(str(obj.cost.amount))
            safe_currency = conditional_escape(obj.cost.currency)
            html = (
                f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                f'bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full">'
                f"{safe_amount} {safe_currency}"
                f"</span>"
            )
            return mark_safe(html)
        return mark_safe(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full">'
            "Free"
            "</span>"
        )

    cost_display.short_description = _("Cost")

    def free_threshold_display(self, obj):
        if obj.free_threshold and obj.free_threshold.amount > 0:
            safe_amount = conditional_escape(str(obj.free_threshold.amount))
            safe_currency = conditional_escape(obj.free_threshold.currency)
            html = (
                f'<span class="text-sm text-base-700 dark:text-base-300">'
                f"Free above {safe_amount} {safe_currency}"
                f"</span>"
            )
            return mark_safe(html)
        return mark_safe(
            '<span class="text-base-400 dark:text-base-500">No threshold</span>'
        )

    free_threshold_display.short_description = _("Free Threshold")

    def configuration_status(self, obj):
        if not obj.is_online_payment:
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-base-50 dark:bg-base-900 text-base-700 dark:text-base-300 rounded-full">'
                "N/A"
                "</span>"
            )
        if obj.is_configured:
            return mark_safe(
                '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full gap-1">'
                "<span>✓</span><span>Configured</span>"
                "</span>"
            )
        return mark_safe(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full gap-1">'
            "<span>⚠️</span><span>Missing Config</span>"
            "</span>"
        )

    configuration_status.short_description = _("Configuration")

    def sort_order_display(self, obj):
        if obj.sort_order is not None:
            safe_order = conditional_escape(str(obj.sort_order))
            html = (
                f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                f'bg-base-50 dark:bg-base-900 text-base-700 dark:text-base-300 rounded-full">'
                f"#{safe_order}"
                f"</span>"
            )
            return mark_safe(html)
        return mark_safe(
            '<span class="text-base-400 dark:text-base-500">-</span>'
        )

    sort_order_display.short_description = _("Order")

    def icon_preview(self, obj):
        if obj.icon:
            safe_url = conditional_escape(obj.icon.url)
            html = (
                f'<img src="{safe_url}" style="max-height: 32px; max-width: 64px; '
                'border-radius: 4px; object-fit: contain;" />'
            )
            return mark_safe(html)
        return mark_safe(
            '<span class="text-base-400 dark:text-base-500">No icon</span>'
        )

    icon_preview.short_description = _("Icon")

    def configuration_preview(self, obj):
        if not obj.configuration:
            return mark_safe(
                '<span class="text-base-400 dark:text-base-500 italic">No configuration</span>'
            )
        keys = list(obj.configuration.keys())
        if len(keys) > 3:
            display = keys[:3] + [f"... and {len(keys) - 3} more"]
        else:
            display = keys
        safe_list = conditional_escape(", ".join(display))
        html = (
            '<div class="text-sm">'
            '<div class="font-medium text-base-700 dark:text-base-300">Configuration Keys:</div>'
            f'<div class="text-base-500 dark:text-base-400">{safe_list}</div>'
            "</div>"
        )
        return mark_safe(html)

    configuration_preview.short_description = _("Configuration Preview")

    def effective_cost_display(self, obj):
        if obj.cost:
            formatted = f"{obj.effective_cost} {obj.cost.currency}"
        else:
            formatted = "0"
        safe_fmt = conditional_escape(formatted)
        html = (
            f'<span class="text-sm font-medium text-base-700 dark:text-base-300">'
            f"{safe_fmt}"
            "</span>"
        )
        return mark_safe(html)

    effective_cost_display.short_description = _("Effective Cost")

    def is_configured_status(self, obj):
        if obj.is_configured:
            return mark_safe(
                '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full gap-1">'
                "<span>✓</span><span>Ready to use</span>"
                "</span>"
            )
        return mark_safe(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full gap-1">'
            "<span>⚠️</span><span>Requires setup</span>"
            "</span>"
        )

    is_configured_status.short_description = _("Ready Status")

    @action(
        description=_("Activate selected payment methods"),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def activate_payment_methods(self, request, queryset):
        updated = queryset.update(active=True)
        self.message_user(
            request,
            _("%(count)d payment methods were successfully activated.")
            % {"count": updated},
        )

    @action(
        description=_("Deactivate selected payment methods"),
        variant=ActionVariant.WARNING,
        icon="cancel",
    )
    def deactivate_payment_methods(self, request, queryset):
        updated = queryset.update(active=False)
        self.message_user(
            request,
            _("%(count)d payment methods were successfully deactivated.")
            % {"count": updated},
        )

    @action(
        description=_("Move selected items up in sort order"),
        variant=ActionVariant.INFO,
        icon="keyboard_arrow_up",
    )
    def move_up_in_order(self, request, queryset):
        moved_count = 0
        for obj in queryset.order_by("sort_order"):
            if obj.sort_order and obj.sort_order > 0:
                obj.move_up()
                moved_count += 1
        self.message_user(
            request,
            _("%(count)d payment methods moved up in sort order.")
            % {"count": moved_count},
        )

    @action(
        description=_("Move selected items down in sort order"),
        variant=ActionVariant.INFO,
        icon="keyboard_arrow_down",
    )
    def move_down_in_order(self, request, queryset):
        moved_count = 0
        for obj in queryset.order_by("-sort_order"):
            obj.move_down()
            moved_count += 1
        self.message_user(
            request,
            _("%(count)d payment methods moved down in sort order.")
            % {"count": moved_count},
        )

    @action(
        description=_("Reset sort order to default"),
        variant=ActionVariant.INFO,
        icon="sort",
    )
    def reset_sort_order(self, request, queryset):
        for index, obj in enumerate(queryset.order_by("id"), start=1):
            obj.sort_order = index * 10
            obj.save(update_fields=["sort_order"])
        self.message_user(
            request,
            _("Sort order has been reset for %(count)d payment methods.")
            % {"count": queryset.count()},
        )
