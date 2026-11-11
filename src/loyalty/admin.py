from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    AutocompleteSelectFilter,
    RangeDateTimeFilter,
)
from unfold.decorators import display

from admin.base import BaseModelAdmin, BaseTranslatableAdmin
from admin.displays import header_two_line
from admin.export import ExportActionMixin
from loyalty.models.tier import LoyaltyTier
from loyalty.models.transaction import PointsTransaction


@admin.register(LoyaltyTier)
class LoyaltyTierAdmin(BaseTranslatableAdmin):
    ordering_field = "sort_order"
    hide_ordering_field = True

    list_display = (
        "name_display",
        "required_level",
        "points_multiplier",
    )
    search_fields = ("translations__name",)
    ordering = ("required_level",)

    @display(description=_("Name"), header=True)
    def name_display(self, obj):
        return header_two_line(
            obj.safe_translation_getter("name", any_language=True),
            image_path=obj.icon.url if obj.icon else None,
            contained=True,
        )


@admin.register(PointsTransaction)
class PointsTransactionAdmin(ExportActionMixin, BaseModelAdmin):
    actions = ["export_csv", "export_xml"]

    list_display = (
        "user",
        "points",
        "transaction_type",
        "reference_order",
        "created_at",
    )
    list_filter = (
        "transaction_type",
        ("user", AutocompleteSelectFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = (
        "user__email",
        "description",
    )
    list_select_related = ("user", "reference_order")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
