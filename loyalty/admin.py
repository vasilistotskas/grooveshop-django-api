from django.contrib import admin
from parler.admin import TranslatableAdmin
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import (
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)

from loyalty.models.tier import LoyaltyTier
from loyalty.models.transaction import PointsTransaction


@admin.register(LoyaltyTier)
class LoyaltyTierAdmin(ModelAdmin, TranslatableAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True

    list_display = (
        "name",
        "required_level",
        "points_multiplier",
    )
    search_fields = ("translations__name",)
    ordering = ("required_level",)


@admin.register(PointsTransaction)
class PointsTransactionAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True

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

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
