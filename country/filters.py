from django.db import models
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from country.models import Country


class CountryFilter(filters.FilterSet):
    id = filters.UUIDFilter(
        field_name="id",
        lookup_expr="exact",
        help_text=_("Filter by country UUID"),
    )
    alpha_2 = filters.CharFilter(
        field_name="alpha_2",
        lookup_expr="icontains",
        help_text=_("Filter by 2-letter country code (partial match)"),
    )
    alpha_3 = filters.CharFilter(
        field_name="alpha_3",
        lookup_expr="icontains",
        help_text=_("Filter by 3-letter country code (partial match)"),
    )
    name = filters.CharFilter(
        field_name="translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by country name (partial match)"),
    )
    iso_cc = filters.NumberFilter(
        field_name="iso_cc",
        lookup_expr="exact",
        help_text=_("Filter by ISO country code"),
    )
    phone_code = filters.NumberFilter(
        field_name="phone_code",
        lookup_expr="exact",
        help_text=_("Filter by phone code"),
    )

    iso_cc_min = filters.NumberFilter(
        field_name="iso_cc",
        lookup_expr="gte",
        help_text=_("Filter countries with ISO code greater than or equal to"),
    )
    iso_cc_max = filters.NumberFilter(
        field_name="iso_cc",
        lookup_expr="lte",
        help_text=_("Filter countries with ISO code less than or equal to"),
    )
    phone_code_min = filters.NumberFilter(
        field_name="phone_code",
        lookup_expr="gte",
        help_text=_(
            "Filter countries with phone code greater than or equal to"
        ),
    )
    phone_code_max = filters.NumberFilter(
        field_name="phone_code",
        lookup_expr="lte",
        help_text=_("Filter countries with phone code less than or equal to"),
    )

    has_iso_cc = filters.BooleanFilter(
        method="filter_has_iso_cc",
        help_text=_("Filter countries that have ISO country code"),
    )
    has_phone_code = filters.BooleanFilter(
        method="filter_has_phone_code",
        help_text=_("Filter countries that have phone code"),
    )
    has_flag_image = filters.BooleanFilter(
        method="filter_has_flag_image",
        help_text=_("Filter countries that have flag image"),
    )

    created_after = filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
        help_text=_("Filter countries created after this date"),
    )
    created_before = filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="lte",
        help_text=_("Filter countries created before this date"),
    )

    class Meta:
        model = Country
        fields = [
            "id",
            "alpha_2",
            "alpha_3",
            "name",
            "iso_cc",
            "phone_code",
            "iso_cc_min",
            "iso_cc_max",
            "phone_code_min",
            "phone_code_max",
            "has_iso_cc",
            "has_phone_code",
            "has_flag_image",
            "created_after",
            "created_before",
        ]

    def filter_has_iso_cc(self, queryset, name, value):
        if value is not None:
            if value:
                return queryset.filter(iso_cc__isnull=False)
            else:
                return queryset.filter(iso_cc__isnull=True)
        return queryset

    def filter_has_phone_code(self, queryset, name, value):
        if value is not None:
            if value:
                return queryset.filter(phone_code__isnull=False)
            else:
                return queryset.filter(phone_code__isnull=True)
        return queryset

    def filter_has_flag_image(self, queryset, name, value):
        if value is not None:
            if value:
                return queryset.exclude(image_flag__isnull=True).exclude(
                    image_flag=""
                )
            else:
                return queryset.filter(
                    models.Q(image_flag__isnull=True) | models.Q(image_flag="")
                )
        return queryset
