from django.db import connection
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from rest_framework import serializers

from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import (
    SortableFilterMixin,
    UUIDFilterMixin,
)
from region.models import Region

SHIPPABLE_HELP = _(
    "true: only the regions the current store delivers to at home "
    "(its shipping zones); false: only the others. On the platform host, "
    "which has no store, true returns none."
)


def filter_shippable(
    queryset: QuerySet[Region], value: bool | None
) -> QuerySet[Region]:
    """``True`` keeps the regions a home delivery of the store reaches
    (``shipping.zones.HomeCoverage``), ``False`` the others, ``None``
    all. The platform host has no store, so no zones: ``True`` there is
    none, as ``/country?shippable=true`` is."""
    if value is None:
        return queryset
    if connection.schema_name == "public":
        return queryset.none() if value else queryset

    from shipping.zones import HomeCoverage

    served = HomeCoverage.load().served_regions_q()
    return queryset.filter(served) if value else queryset.exclude(served)


class ShippableQuerySerializer(serializers.Serializer):
    """The ``shippable`` query parameter of a region list that takes no
    filter set (``get_regions_by_country_alpha_2``)."""

    shippable = serializers.BooleanField(
        required=False, allow_null=True, default=None, help_text=SHIPPABLE_HELP
    )


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

    shippable = filters.BooleanFilter(
        method="filter_shippable", help_text=SHIPPABLE_HELP
    )

    class Meta:
        model = Region
        fields = {
            "alpha": ["exact", "icontains"],
            "country": ["exact"],
            "sort_order": ["exact", "gte", "lte"],
            "uuid": ["exact"],
        }

    def filter_shippable(self, queryset, name, value):
        return filter_shippable(queryset, value)
