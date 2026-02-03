from django_filters import rest_framework as filters

from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin, SortableFilterMixin
from product.models.attribute import Attribute


class AttributeFilter(
    UUIDFilterMixin,
    SortableFilterMixin,
    CamelCaseTimeStampFilterSet,
):
    """Filter for Attribute model."""

    id = filters.NumberFilter(field_name="id")
    id__in = filters.BaseInFilter(
        field_name="id",
        help_text="Filter by multiple attribute IDs (comma-separated)",
    )
    active = filters.BooleanFilter(field_name="active")
    name = filters.CharFilter(
        field_name="translations__name", lookup_expr="icontains"
    )
    has_values = filters.BooleanFilter(method="filter_has_values")

    sort_order = filters.NumberFilter(
        field_name="sort_order",
        help_text="Filter by exact sort order",
    )
    sort_order_min = filters.NumberFilter(
        field_name="sort_order",
        lookup_expr="gte",
        help_text="Minimum sort order",
    )
    sort_order_max = filters.NumberFilter(
        field_name="sort_order",
        lookup_expr="lte",
        help_text="Maximum sort order",
    )

    class Meta:
        model = Attribute
        fields = {
            "id": ["exact"],
            "active": ["exact"],
            "sort_order": ["exact"],
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
        }

    def filter_has_values(self, queryset, name, value):
        """Filter attributes that have/don't have values."""
        if value:
            return queryset.filter(values__isnull=False).distinct()
        return queryset.filter(values__isnull=True)
