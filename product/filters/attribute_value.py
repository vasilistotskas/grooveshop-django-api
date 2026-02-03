from django_filters import rest_framework as filters

from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin, SortableFilterMixin
from product.models.attribute_value import AttributeValue


class AttributeValueFilter(
    UUIDFilterMixin,
    SortableFilterMixin,
    CamelCaseTimeStampFilterSet,
):
    """Filter for AttributeValue model."""

    id = filters.NumberFilter(field_name="id")
    id__in = filters.BaseInFilter(
        field_name="id",
        help_text="Filter by multiple attribute value IDs (comma-separated)",
    )
    attribute = filters.NumberFilter(field_name="attribute_id")
    attribute__in = filters.BaseInFilter(
        field_name="attribute_id",
        help_text="Filter by multiple attribute IDs (comma-separated)",
    )
    active = filters.BooleanFilter(field_name="active")
    value = filters.CharFilter(
        field_name="translations__value", lookup_expr="icontains"
    )

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
        model = AttributeValue
        fields = {
            "id": ["exact"],
            "attribute": ["exact"],
            "active": ["exact"],
            "sort_order": ["exact"],
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
        }
