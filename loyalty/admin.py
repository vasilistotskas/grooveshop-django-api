from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)

from admin.base import BaseModelAdmin, BaseTranslatableAdmin
from admin.export import ExportActionMixin
from loyalty.models.tier import LoyaltyTier
from loyalty.models.transaction import PointsTransaction


@admin.register(LoyaltyTier)
class LoyaltyTierAdmin(BaseTranslatableAdmin):
    ordering_field = "sort_order"
    hide_ordering_field = True

    list_display = (
        "name",
        "required_level",
        "points_multiplier",
        "icon_preview",
    )
    search_fields = ("translations__name",)
    ordering = ("required_level",)

    @admin.display(description=_("Icon"), empty_value="—")
    def icon_preview(self, obj):
        if not obj.icon:
            return None
        return format_html(
            '<img src="{url}" width="64" height="32" alt="" />',
            url=obj.icon.url,
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
        ("user", RelatedDropdownFilter),
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
