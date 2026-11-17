from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from unfold.admin import TabularInline
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
from product.models.attribute import Attribute
from product.models.attribute_value import AttributeValue


class AttributeValueInline(TabularInline):
    """Inline for managing attribute values within attribute admin."""

    model = AttributeValue
    extra = 0
    fields = ("value_display", "active", "sort_order", "usage_count_display")
    readonly_fields = ("value_display", "usage_count_display")
    ordering_field = "sort_order"
    hide_ordering_field = True

    tab = True
    show_change_link = True

    @admin.display(description=_("Value"))
    def value_display(self, obj):
        if not obj.pk:
            return "-"
        return obj.safe_translation_getter("value", any_language=True) or _(
            "Unnamed"
        )

    def get_queryset(self, request):
        # ``usage_count_display`` below counted per row — 6 COUNT queries
        # for 6 values on the Attribute change page. ``AttributeValueAdmin``
        # solves the same problem with the same annotation; the inline
        # was missed.
        return super().get_queryset(request).with_usage_count()

    @admin.display(description=_("Usage"))
    def usage_count_display(self, obj):
        if not obj.pk:
            return "-"
        annotated = getattr(obj, "usage_count", None)
        if annotated is not None:
            return annotated
        return obj.product_attributes.count()


@admin.register(Attribute)
class AttributeAdmin(BaseTranslatableAdmin):
    """Admin interface for managing product attributes."""

    ordering_field = "sort_order"
    hide_ordering_field = True
    list_display = (
        "attribute_info",
        "active",
        "is_variant",
        "schema_property",
        "values_count_display",
        "usage_count_display",
        "sort_order",
        "created_display",
    )
    search_fields = [
        "id",
        "translations__name",
    ]
    list_filter = [
        "active",
        "is_variant",
        "schema_property",
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
    ]
    inlines = [AttributeValueInline]
    readonly_fields = (
        "id",
        "uuid",
        "created_at",
        "updated_at",
        "values_count_display",
        "usage_count_display",
    )
    actions = [
        "activate_attributes",
        "deactivate_attributes",
    ]
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Attribute Information"),
            {
                "fields": ("name", "active", "is_variant", "schema_property"),
            },
        ),
        (
            _("Statistics"),
            {
                "fields": ("values_count_display", "usage_count_display"),
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
            .with_values_count()
            .with_usage_count()
            .prefetch_related("translations", "values")
        )

    @admin.display(description=_("Attribute"), ordering="translations__name")
    def attribute_info(self, obj):
        name = obj.safe_translation_getter("name", any_language=True) or _(
            "Unnamed"
        )
        return f"{name} (#{obj.id})"

    @display(description=_("Values"), ordering="values_count")
    def values_count_display(self, obj):
        return getattr(obj, "values_count", 0)

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
        description=_("Activate selected attributes"),
        permissions=["change"],
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def activate_attributes(self, request, queryset):
        """Bulk action to activate selected attributes."""
        updated = update_each(queryset, active=True)
        self.message_user(
            request,
            ngettext(
                "%(count)d attribute was successfully activated.",
                "%(count)d attributes were successfully activated.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=_("Deactivate selected attributes"),
        permissions=["change"],
        variant=ActionVariant.WARNING,
        icon="cancel",
    )
    def deactivate_attributes(self, request, queryset):
        """Bulk action to deactivate selected attributes."""
        updated = update_each(queryset, active=False)
        self.message_user(
            request,
            ngettext(
                "%(count)d attribute was successfully deactivated.",
                "%(count)d attributes were successfully deactivated.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )
