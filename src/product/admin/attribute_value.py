from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from unfold.contrib.filters.admin import (
    RangeDateTimeFilter,
)
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from admin.base import (
    BaseTranslatableAdmin,
)
from admin.bulk import update_each
from admin.displays import (
    format_dt,
    relative_time,
)
from admin.filters import (
    RelatedDropdownFilter,
)
from product.models.attribute_value import AttributeValue


@admin.register(AttributeValue)
class AttributeValueAdmin(BaseTranslatableAdmin):
    """Admin interface for managing attribute values."""

    ordering_field = "sort_order"
    hide_ordering_field = True
    list_display = (
        "value_info",
        "attribute_display",
        "active",
        "usage_count_display",
        "sort_order",
        "created_display",
    )
    search_fields = [
        "id",
        "translations__value",
        "attribute__translations__name",
    ]
    list_filter = [
        "active",
        ("attribute", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
    ]
    readonly_fields = (
        "id",
        "uuid",
        "created_at",
        "updated_at",
        "usage_count_display",
    )
    list_select_related = ["attribute"]
    actions = [
        "activate_values",
        "deactivate_values",
    ]
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Value Information"),
            {
                "fields": ("attribute", "value", "active"),
            },
        ),
        (
            _("Statistics"),
            {
                "fields": ("usage_count_display",),
                "classes": ("collapse",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": (
                    "id",
                    "uuid",
                    "sort_order",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        """Optimize queryset with annotations."""
        return (
            super()
            .get_queryset(request)
            .with_usage_count()
            .select_related("attribute")
            .prefetch_related("translations", "attribute__translations")
        )

    @admin.display(description=_("Value"), ordering="translations__value")
    def value_info(self, obj):
        value = obj.safe_translation_getter("value", any_language=True) or _(
            "Unnamed"
        )
        return f"{value} (#{obj.id})"

    @admin.display(description=_("Attribute"))
    def attribute_display(self, obj):
        if not obj.attribute:
            return _("No Attribute")
        return obj.attribute.safe_translation_getter(
            "name", any_language=True
        ) or _("Unnamed")

    @display(description=_("Usage"), ordering="usage_count")
    def usage_count_display(self, obj):
        return getattr(obj, "usage_count", 0)

    @display(description=_("Created"), ordering="created_at")
    def created_display(self, obj):
        return (
            f"{format_dt(obj.created_at, fmt='SHORT_DATE_FORMAT')} "
            f"({relative_time(obj.created_at)})"
        )

    @action(
        description=_("Activate selected values"),
        permissions=["change"],
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def activate_values(self, request, queryset):
        """Bulk action to activate selected attribute values."""
        updated = update_each(queryset, active=True)
        self.message_user(
            request,
            ngettext(
                "%(count)d attribute value was successfully activated.",
                "%(count)d attribute values were successfully activated.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=_("Deactivate selected values"),
        permissions=["change"],
        variant=ActionVariant.WARNING,
        icon="cancel",
    )
    def deactivate_values(self, request, queryset):
        """Bulk action to deactivate selected attribute values."""
        updated = update_each(queryset, active=False)
        self.message_user(
            request,
            ngettext(
                "%(count)d attribute value was successfully deactivated.",
                "%(count)d attribute values were successfully deactivated.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )
