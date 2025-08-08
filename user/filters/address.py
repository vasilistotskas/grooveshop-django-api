from django.db import models
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin
from user.models.address import UserAddress


class UserAddressFilter(UUIDFilterMixin, CamelCaseTimeStampFilterSet):
    location_type = filters.CharFilter(
        field_name="location_type",
        lookup_expr="iexact",
        help_text=_("Filter by location type (exact match, case insensitive)"),
    )
    location_type_contains = filters.CharFilter(
        field_name="location_type",
        lookup_expr="icontains",
        help_text="Filter by location type (partial match)",
    )

    country = filters.CharFilter(
        field_name="country__alpha_2",
        lookup_expr="exact",
        help_text=_("Filter by country alpha_2 code"),
    )
    country_code = filters.CharFilter(
        field_name="country__alpha_2",
        lookup_expr="iexact",
        help_text=_("Filter by country code (e.g., 'US', 'CA')"),
    )
    country_name = filters.CharFilter(
        field_name="country__translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by country name (partial match)"),
    )

    region = filters.CharFilter(
        field_name="region__alpha",
        lookup_expr="exact",
        help_text=_("Filter by region alpha code"),
    )
    region_code = filters.CharFilter(
        field_name="region__alpha",
        lookup_expr="iexact",
        help_text=_("Filter by region code"),
    )
    region_name = filters.CharFilter(
        field_name="region__translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by region name (partial match)"),
    )

    city = filters.CharFilter(
        field_name="city",
        lookup_expr="icontains",
        help_text=_("Filter by city name (partial match)"),
    )
    zipcode = filters.CharFilter(
        field_name="zipcode",
        lookup_expr="icontains",
        help_text=_("Filter by zipcode (partial match)"),
    )
    zipcode_exact = filters.CharFilter(
        field_name="zipcode",
        lookup_expr="exact",
        help_text=_("Filter by exact zipcode"),
    )

    street = filters.CharFilter(
        field_name="street",
        lookup_expr="icontains",
        help_text=_("Filter by street name (partial match)"),
    )
    street_number = filters.CharFilter(
        field_name="street_number",
        lookup_expr="icontains",
        help_text=_("Filter by street number"),
    )

    first_name = filters.CharFilter(
        field_name="first_name",
        lookup_expr="icontains",
        help_text=_("Filter by first name (partial match)"),
    )
    last_name = filters.CharFilter(
        field_name="last_name",
        lookup_expr="icontains",
        help_text=_("Filter by last name (partial match)"),
    )
    full_name = filters.CharFilter(
        method="filter_full_name",
        help_text=_("Filter by full name (first + last name)"),
    )

    is_main = filters.BooleanFilter(
        field_name="is_main",
        lookup_expr="exact",
        help_text=_("Filter by main address status"),
    )

    phone = filters.CharFilter(
        field_name="phone",
        lookup_expr="icontains",
        help_text=_("Filter by phone number (partial match)"),
    )
    mobile_phone = filters.CharFilter(
        field_name="mobile_phone",
        lookup_expr="icontains",
        help_text=_("Filter by mobile phone number (partial match)"),
    )

    floor = filters.ChoiceFilter(
        field_name="floor",
        help_text=_("Filter by floor"),
    )

    has_notes = filters.BooleanFilter(
        method="filter_has_notes",
        help_text=_("Filter addresses that have notes"),
    )

    class Meta:
        model = UserAddress
        fields = {
            "id": ["exact", "in"],
            "title": ["exact", "icontains"],
            "location_type": ["exact", "icontains"],
            "country": ["exact"],
            "region": ["exact"],
            "city": ["exact", "icontains"],
            "zipcode": ["exact", "icontains"],
            "street": ["exact", "icontains"],
            "street_number": ["exact", "icontains"],
            "first_name": ["exact", "icontains"],
            "last_name": ["exact", "icontains"],
            "is_main": ["exact"],
            "phone": ["exact", "icontains"],
            "mobile_phone": ["exact", "icontains"],
            "floor": ["exact"],
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.enum import FloorChoicesEnum

        self.filters["floor"].extra["choices"] = FloorChoicesEnum.choices

    def filter_full_name(self, queryset, name, value):
        if not value:
            return queryset

        names = value.strip().split()
        if len(names) == 1:
            return queryset.filter(
                models.Q(first_name__icontains=names[0])
                | models.Q(last_name__icontains=names[0])
            )
        elif len(names) >= 2:
            return queryset.filter(
                first_name__icontains=names[0], last_name__icontains=names[-1]
            )
        return queryset

    def filter_has_notes(self, queryset, name, value):
        if value is True:
            return queryset.exclude(notes="")
        elif value is False:
            return queryset.filter(notes="")
        return queryset
