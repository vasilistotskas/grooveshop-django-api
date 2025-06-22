from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from region.models import Region


class RegionFilter(filters.FilterSet):
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
    created_after = filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
        help_text=_("Filter regions created after this date"),
    )
    created_before = filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="lte",
        help_text=_("Filter regions created before this date"),
    )
    updated_after = filters.DateTimeFilter(
        field_name="updated_at",
        lookup_expr="gte",
        help_text=_("Filter regions updated after this date"),
    )
    updated_before = filters.DateTimeFilter(
        field_name="updated_at",
        lookup_expr="lte",
        help_text=_("Filter regions updated before this date"),
    )

    class Meta:
        model = Region
        fields = {
            "alpha": ["exact", "icontains"],
            "country": ["exact"],
            "created_at": ["gte", "lte"],
            "updated_at": ["gte", "lte"],
        }
