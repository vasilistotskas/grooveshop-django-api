"""Filters of the BOX NOW locker list (``GET /shipping/boxnow/lockers``)."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from core.filters.camel_case_filters import CamelCaseFilterMixin
from shipping_boxnow.enum.compartment_size import BoxNowCompartmentSize
from shipping_boxnow.models import BoxNowLocker


class BoxNowLockerFilter(CamelCaseFilterMixin, filters.FilterSet):
    """Exact lookups a client uses to find one locker it already names
    (the agent gateway resolving a UCP pickup destination)."""

    external_id = filters.CharFilter(
        field_name="external_id",
        lookup_expr="exact",
        help_text=_("The locker's BOX NOW id, exactly."),
    )
    country_code = filters.CharFilter(
        field_name="country_code",
        lookup_expr="iexact",
        help_text=_("ISO 3166-1 alpha-2 country of the locker."),
    )

    compartment_size = filters.TypedChoiceFilter(
        choices=BoxNowCompartmentSize.choices,
        coerce=int,
        method="filter_compartment_size",
        help_text=_(
            "Only lockers that have a compartment of this size (1 small, "
            "2 medium, 3 large), by exact membership as BOX NOW's own "
            "picker does. A locker whose sizes are not known never matches."
        ),
    )

    def filter_compartment_size(self, queryset, name, value):
        return queryset.filter(compartment_sizes__contains=[value])

    class Meta:
        model = BoxNowLocker
        fields = ("external_id", "country_code", "compartment_size")
