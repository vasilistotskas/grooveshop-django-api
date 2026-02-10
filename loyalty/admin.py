from django.contrib import admin
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
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
        "icon_preview",
    )
    search_fields = ("translations__name",)
    ordering = ("required_level",)

    def icon_preview(self, obj):
        if obj.icon:
            safe_url = conditional_escape(obj.icon.url)
            html = (
                f'<img src="{safe_url}" style="max-height: 32px; max-width: 64px; '
                'border-radius: 4px; object-fit: contain;" />'
            )
            return mark_safe(html)
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300">No icon</span>'
        )

    icon_preview.short_description = _("Icon")


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
