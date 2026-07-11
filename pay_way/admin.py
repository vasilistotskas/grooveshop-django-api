from django.contrib import admin
from django.db.models import Q
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import TabularInline
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
    RangeNumericListFilter,
)
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from admin.base import BaseTranslatableAdmin
from pay_way.models import PayWay, PayWayShippingExclusion

PAYMENT_TYPE_VARIANT: dict[str, str] = {
    "online": "info",
    "offline_confirmation": "warning",
    "offline_simple": "default",
}


class PayWayShippingExclusionInline(TabularInline):
    """Reusable inline for ``PayWayShippingExclusion``.

    Registered on both ``PayWayAdmin`` (rows where ``pay_way=this``)
    and ``ShippingProviderAdmin`` (rows where
    ``shipping_provider=this``). Both edit the same table — pick
    whichever side you're already on in the admin.

    Why we don't constrain ``shipping_kind`` at form-render time to
    only the kinds the parent provider supports: the inline is also
    used from the PayWay side where there's no single parent
    provider, so a uniform full-choice dropdown keeps the UX
    consistent. The ``unique_together`` constraint + the carrier's
    ``ShippingProvider.supports`` check at runtime already prevent
    misconfigured combinations from doing damage.
    """

    model = PayWayShippingExclusion
    extra = 0
    fields = ("shipping_provider", "shipping_kind", "pay_way", "note")
    autocomplete_fields = ("pay_way", "shipping_provider")
    verbose_name = _("Payment-method exclusion")
    verbose_name_plural = _("Payment-method exclusions")


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
class PayWayAdmin(BaseTranslatableAdmin):
    list_display = [
        "name_display",
        "provider_code_display",
        "payment_type_display",
        "cost_display",
        "free_threshold_display",
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

    search_fields = [
        "translations__name",
        "provider_code",
        "translations__description",
        "translations__instructions",
    ]

    ordering_field = "sort_order"
    hide_ordering_field = True
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "configuration",
        "configuration_preview",
        "effective_cost_display",
        "is_configured_status",
    ]

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        # Superusers may edit the raw configuration JSON directly.
        if request.user.is_superuser and "configuration" in fields:
            fields.remove("configuration")
        return fields

    ordering = ["sort_order", "id"]

    actions = [
        "activate_payment_methods",
        "deactivate_payment_methods",
        "move_up_in_order",
        "move_down_in_order",
        "reset_sort_order",
    ]

    inlines = [PayWayShippingExclusionInline]

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

    @admin.display(description=_("Name"))
    def name_display(self, obj):
        return obj.safe_translation_getter("name", any_language=True) or _(
            "Unnamed Payment Method"
        )

    @admin.display(description=_("Provider"))
    def provider_code_display(self, obj):
        return obj.provider_code or _("No provider")

    @display(description=_("Type"), label=PAYMENT_TYPE_VARIANT)
    def payment_type_display(self, obj):
        if obj.is_online_payment:
            return "online", _("Online")
        if obj.requires_confirmation:
            return "offline_confirmation", _("Offline (Confirm)")
        return "offline_simple", _("Offline")

    @admin.display(description=_("Cost"))
    def cost_display(self, obj):
        if obj.cost and obj.cost.amount > 0:
            return f"{obj.cost.amount} {obj.cost.currency}"
        return _("Free")

    @admin.display(description=_("Free Threshold"))
    def free_threshold_display(self, obj):
        if obj.free_threshold and obj.free_threshold.amount > 0:
            return _("Free above %(amount)s %(currency)s") % {
                "amount": obj.free_threshold.amount,
                "currency": obj.free_threshold.currency,
            }
        return _("No threshold")

    @admin.display(description=_("Icon"))
    def icon_preview(self, obj):
        if obj.icon:
            return format_html(
                '<img src="{url}" class="h-8 max-w-16 object-contain" />',
                url=obj.icon.url,
            )
        return _("No icon")

    @admin.display(description=_("Configuration Preview"))
    def configuration_preview(self, obj):
        if not obj.configuration:
            return _("No configuration")
        keys = list(obj.configuration.keys())
        if len(keys) > 3:
            shown = [*keys[:3], _("... and %(n)d more") % {"n": len(keys) - 3}]
        else:
            shown = keys
        return _("Configuration keys: %(keys)s") % {"keys": ", ".join(shown)}

    @admin.display(description=_("Effective Cost"))
    def effective_cost_display(self, obj):
        if obj.cost:
            return f"{obj.effective_cost} {obj.cost.currency}"
        return "0"

    @admin.display(description=_("Ready Status"))
    def is_configured_status(self, obj):
        return _("Ready to use") if obj.is_configured else _("Requires setup")

    @admin.display(description=_("Order"))
    def sort_order_display(self, obj):
        if obj.sort_order is not None:
            return f"#{obj.sort_order}"
        return "-"

    @action(
        description=str(_("Activate selected payment methods")),
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
        description=str(_("Deactivate selected payment methods")),
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
        description=str(_("Move selected items up in sort order")),
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
        description=str(_("Move selected items down in sort order")),
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
        description=str(_("Reset sort order to default")),
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
