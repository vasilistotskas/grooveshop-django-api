from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from country.models import Country
from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin, SortableFilterMixin


class CountryFilter(
    UUIDFilterMixin, SortableFilterMixin, CamelCaseTimeStampFilterSet
):
    alpha_2 = filters.CharFilter(
        field_name="alpha_2",
        lookup_expr="iexact",
        help_text=_("Filter by exact 2-letter country code"),
    )
    alpha_2__icontains = filters.CharFilter(
        field_name="alpha_2",
        lookup_expr="icontains",
        help_text=_("Filter by 2-letter country code (partial match)"),
    )
    alpha_3 = filters.CharFilter(
        field_name="alpha_3",
        lookup_expr="iexact",
        help_text=_("Filter by exact 3-letter country code"),
    )
    alpha_3__icontains = filters.CharFilter(
        field_name="alpha_3",
        lookup_expr="icontains",
        help_text=_("Filter by 3-letter country code (partial match)"),
    )

    iso_cc = filters.NumberFilter(
        field_name="iso_cc",
        lookup_expr="exact",
        help_text=_("Filter by exact ISO country code"),
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
    phone_code = filters.NumberFilter(
        field_name="phone_code",
        lookup_expr="exact",
        help_text=_("Filter by exact phone code"),
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

    name = filters.CharFilter(
        field_name="translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by country name (partial match)"),
    )
    name__exact = filters.CharFilter(
        field_name="translations__name",
        lookup_expr="iexact",
        help_text=_("Filter by exact country name"),
    )
    name__startswith = filters.CharFilter(
        field_name="translations__name",
        lookup_expr="istartswith",
        help_text=_("Filter countries with names starting with"),
    )

    multiple_codes = filters.CharFilter(
        method="filter_multiple_codes",
        help_text=_(
            "Filter by multiple country codes (comma-separated, alpha-2 or alpha-3)"
        ),
    )
    continent = filters.ChoiceFilter(
        method="filter_continent",
        choices=[
            ("AF", "Africa"),
            ("AS", "Asia"),
            ("EU", "Europe"),
            ("NA", "North America"),
            ("OC", "Oceania"),
            ("SA", "South America"),
            ("AN", "Antarctica"),
        ],
        help_text=_("Filter by continent (based on ISO codes)"),
    )
    is_eu = filters.BooleanFilter(
        method="filter_is_eu",
        help_text=_("Filter EU member countries"),
    )

    has_iso_cc = filters.BooleanFilter(
        method="filter_has_iso_cc",
        help_text=_("Filter countries that have/don't have ISO country code"),
    )
    has_phone_code = filters.BooleanFilter(
        method="filter_has_phone_code",
        help_text=_("Filter countries that have/don't have phone code"),
    )
    has_flag_image = filters.BooleanFilter(
        method="filter_has_flag_image",
        help_text=_("Filter countries that have/don't have flag image"),
    )
    has_name = filters.BooleanFilter(
        method="filter_has_name",
        help_text=_("Filter countries that have/don't have name translation"),
    )
    has_all_data = filters.BooleanFilter(
        method="filter_has_all_data",
        help_text=_(
            "Filter countries that have complete data (ISO, phone, flag, name)"
        ),
    )

    class Meta:
        model = Country
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "sort_order": ["exact", "gte", "lte"],
            "uuid": ["exact"],
            "alpha_2": ["exact", "iexact", "in", "icontains"],
            "alpha_3": ["exact", "iexact", "in", "icontains"],
            "iso_cc": ["exact", "gte", "lte", "in"],
            "phone_code": ["exact", "gte", "lte", "in"],
        }

    def filter_multiple_codes(self, queryset, name, value):
        """Filter by multiple country codes (alpha-2 or alpha-3)."""
        if value:
            codes = [code.strip().upper() for code in value.split(",")]
            return queryset.filter(Q(alpha_2__in=codes) | Q(alpha_3__in=codes))
        return queryset

    def filter_continent(self, queryset, name, value):
        """Filter by continent based on ISO country codes."""
        continent_ranges = {
            "AF": [(12, 894)],
            "AS": [(31, 860)],
            "EU": [(40, 832)],
            "NA": [(124, 850)],
            "OC": [(36, 876)],
            "SA": [(32, 858)],
            "AN": [(10, 10)],
        }

        if value in continent_ranges:
            q = Q()
            for start, end in continent_ranges[value]:
                q |= Q(iso_cc__gte=start, iso_cc__lte=end)
            return queryset.filter(q)
        return queryset

    def filter_is_eu(self, queryset, name, value):
        """Filter EU member countries."""
        eu_codes = [
            "AT",
            "BE",
            "BG",
            "HR",
            "CY",
            "CZ",
            "DK",
            "EE",
            "FI",
            "FR",
            "DE",
            "GR",
            "HU",
            "IE",
            "IT",
            "LV",
            "LT",
            "LU",
            "MT",
            "NL",
            "PL",
            "PT",
            "RO",
            "SK",
            "SI",
            "ES",
            "SE",
        ]

        if value is True:
            return queryset.filter(alpha_2__in=eu_codes)
        elif value is False:
            return queryset.exclude(alpha_2__in=eu_codes)
        return queryset

    def filter_has_iso_cc(self, queryset, name, value):
        """Filter countries that have/don't have ISO country code."""
        if value is True:
            return queryset.exclude(iso_cc__isnull=True)
        elif value is False:
            return queryset.filter(iso_cc__isnull=True)
        return queryset

    def filter_has_phone_code(self, queryset, name, value):
        """Filter countries that have/don't have phone code."""
        if value is True:
            return queryset.exclude(phone_code__isnull=True)
        elif value is False:
            return queryset.filter(phone_code__isnull=True)
        return queryset

    def filter_has_flag_image(self, queryset, name, value):
        """Filter countries that have/don't have flag image."""
        if value is True:
            return queryset.exclude(
                Q(image_flag__isnull=True) | Q(image_flag__exact="")
            )
        elif value is False:
            return queryset.filter(
                Q(image_flag__isnull=True) | Q(image_flag__exact="")
            )
        return queryset

    def filter_has_name(self, queryset, name, value):
        """Filter countries that have/don't have name translation."""
        if value is True:
            return queryset.exclude(
                Q(translations__name__isnull=True)
                | Q(translations__name__exact="")
            ).distinct()
        elif value is False:
            return queryset.filter(
                Q(translations__name__isnull=True)
                | Q(translations__name__exact="")
            ).distinct()
        return queryset

    def filter_has_all_data(self, queryset, name, value):
        """Filter countries that have complete data (ISO, phone, flag, name)."""
        if value is True:
            return queryset.exclude(
                Q(iso_cc__isnull=True)
                | Q(phone_code__isnull=True)
                | Q(image_flag__isnull=True)
                | Q(image_flag__exact="")
                | Q(translations__name__isnull=True)
                | Q(translations__name__exact="")
            ).distinct()
        elif value is False:
            return queryset.filter(
                Q(iso_cc__isnull=True)
                | Q(phone_code__isnull=True)
                | Q(image_flag__isnull=True)
                | Q(image_flag__exact="")
                | Q(translations__name__isnull=True)
                | Q(translations__name__exact="")
            ).distinct()
        return queryset
