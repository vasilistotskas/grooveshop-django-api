from django.db import models
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from user.models.address import UserAddress


class UserAddressFilter(filters.FilterSet):
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

    country = filters.NumberFilter(
        field_name="country__id",
        lookup_expr="exact",
        help_text="Filter by country ID",
    )
    country_code = filters.CharFilter(
        field_name="country__code",
        lookup_expr="iexact",
        help_text="Filter by country code (e.g., 'US', 'CA')",
    )
    country_name = filters.CharFilter(
        field_name="country__name",
        lookup_expr="icontains",
        help_text="Filter by country name (partial match)",
    )

    region = filters.NumberFilter(
        field_name="region__id",
        lookup_expr="exact",
        help_text="Filter by region ID",
    )
    region_name = filters.CharFilter(
        field_name="region__name",
        lookup_expr="icontains",
        help_text="Filter by region name (partial match)",
    )

    city = filters.CharFilter(
        field_name="city",
        lookup_expr="icontains",
        help_text="Filter by city name (partial match)",
    )
    zipcode = filters.CharFilter(
        field_name="zipcode",
        lookup_expr="icontains",
        help_text="Filter by zipcode (partial match)",
    )
    zipcode_exact = filters.CharFilter(
        field_name="zipcode",
        lookup_expr="exact",
        help_text="Filter by exact zipcode",
    )

    street = filters.CharFilter(
        field_name="street",
        lookup_expr="icontains",
        help_text="Filter by street name (partial match)",
    )
    street_number = filters.CharFilter(
        field_name="street_number",
        lookup_expr="icontains",
        help_text="Filter by street number",
    )

    first_name = filters.CharFilter(
        field_name="first_name",
        lookup_expr="icontains",
        help_text="Filter by first name (partial match)",
    )
    last_name = filters.CharFilter(
        field_name="last_name",
        lookup_expr="icontains",
        help_text="Filter by last name (partial match)",
    )
    full_name = filters.CharFilter(
        method="filter_full_name",
        help_text="Filter by full name (first + last name)",
    )

    is_main = filters.BooleanFilter(
        field_name="is_main",
        lookup_expr="exact",
        help_text="Filter by main address status",
    )

    phone = filters.CharFilter(
        field_name="phone",
        lookup_expr="icontains",
        help_text="Filter by phone number (partial match)",
    )
    mobile_phone = filters.CharFilter(
        field_name="mobile_phone",
        lookup_expr="icontains",
        help_text="Filter by mobile phone number (partial match)",
    )

    created_after = filters.DateFilter(
        field_name="created_at",
        lookup_expr="gte",
        help_text="Filter addresses created after this date",
    )
    created_before = filters.DateFilter(
        field_name="created_at",
        lookup_expr="lte",
        help_text="Filter addresses created before this date",
    )
    updated_after = filters.DateFilter(
        field_name="updated_at",
        lookup_expr="gte",
        help_text="Filter addresses updated after this date",
    )
    updated_before = filters.DateFilter(
        field_name="updated_at",
        lookup_expr="lte",
        help_text="Filter addresses updated before this date",
    )

    class Meta:
        model = UserAddress
        fields = [
            "id",
            "title",
            "location_type",
            "location_type_contains",
            "country",
            "country_code",
            "country_name",
            "region",
            "region_name",
            "city",
            "zipcode",
            "zipcode_exact",
            "street",
            "street_number",
            "first_name",
            "last_name",
            "full_name",
            "is_main",
            "phone",
            "mobile_phone",
            "created_after",
            "created_before",
            "updated_after",
            "updated_before",
        ]

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
