from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import (
    SortableFilterMixin,
    UUIDFilterMixin,
)
from region.models import Region


class RegionFilter(
    SortableFilterMixin, UUIDFilterMixin, CamelCaseTimeStampFilterSet
):
    alpha = filters.CharFilter(
        field_name="alpha",
        lookup_expr="icontains",
        help_text=_("Filter by region alpha code (partial match)"),
    )
    alpha_exact = filters.CharFilter(
        field_name="alpha",
        lookup_expr="exact",
        help_text=_("Filter by exact region alpha code"),
    )
    country = filters.CharFilter(
        field_name="country__alpha_2",
        lookup_expr="exact",
        help_text=_("Filter by country alpha-2 code"),
    )
    country_name = filters.CharFilter(
        field_name="country__translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by country name (partial match)"),
    )
    name = filters.CharFilter(
        field_name="translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by region name (partial match)"),
    )

    class Meta:
        model = Region
        fields = {
            "alpha": ["exact", "icontains"],
            "country": ["exact"],
            "sort_order": ["exact", "gte", "lte"],
            "uuid": ["exact"],
        }
