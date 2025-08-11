from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from tag.models.tagged_item import TaggedItem
from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin


class TaggedItemFilter(UUIDFilterMixin, CamelCaseTimeStampFilterSet):
    tag = filters.NumberFilter(
        field_name="tag__id",
        help_text=_("Filter by specific tag ID"),
    )
    tag__label = filters.CharFilter(
        field_name="tag__translations__label",
        lookup_expr="icontains",
        help_text=_("Filter by tag label (partial match)"),
    )
    tag__active = filters.BooleanFilter(
        field_name="tag__active",
        help_text=_("Filter by tag active status"),
    )

    content_type = filters.CharFilter(
        field_name="content_type__model",
        help_text=_("Filter by content type model name"),
    )
    content_type__app_label = filters.CharFilter(
        field_name="content_type__app_label",
        help_text=_("Filter by content type app label"),
    )
    object_id = filters.NumberFilter(
        field_name="object_id",
        help_text=_("Filter by object ID"),
    )

    object_id__in = filters.BaseInFilter(
        field_name="object_id",
        help_text=_("Filter by multiple object IDs (comma-separated)"),
    )

    class Meta:
        model = TaggedItem
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
            "id": ["exact", "in"],
            "tag": ["exact"],
            "object_id": ["exact", "in"],
        }
