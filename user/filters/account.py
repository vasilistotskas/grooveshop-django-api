from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from core.filters.camel_case_filters import CamelCaseFilterMixin
from core.filters.core import TimeStampFilterMixin, UUIDFilterMixin
from user.models.account import UserAccount


class UserAccountFilter(
    TimeStampFilterMixin,
    UUIDFilterMixin,
    CamelCaseFilterMixin,
    filters.FilterSet,
):
    id = filters.NumberFilter(
        field_name="id",
        lookup_expr="exact",
        help_text=_("Filter by user account ID"),
    )
    email = filters.CharFilter(
        field_name="email",
        lookup_expr="icontains",
        help_text=_("Filter by email (partial match)"),
    )
    email_exact = filters.CharFilter(
        field_name="email",
        lookup_expr="exact",
        help_text=_("Filter by exact email"),
    )
    username = filters.CharFilter(
        field_name="username",
        lookup_expr="icontains",
        help_text=_("Filter by username (partial match)"),
    )
    username_exact = filters.CharFilter(
        field_name="username",
        lookup_expr="exact",
        help_text=_("Filter by exact username"),
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
    is_active = filters.BooleanFilter(
        field_name="is_active",
        help_text=_("Filter by active status"),
    )
    is_staff = filters.BooleanFilter(
        field_name="is_staff",
        help_text=_("Filter by staff status"),
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
    region = filters.CharFilter(
        field_name="region__alpha",
        lookup_expr="exact",
        help_text=_("Filter by region alpha code"),
    )
    city = filters.CharFilter(
        field_name="city",
        lookup_expr="icontains",
        help_text=_("Filter by city (partial match)"),
    )
    zipcode = filters.CharFilter(
        field_name="zipcode",
        lookup_expr="icontains",
        help_text=_("Filter by zipcode (partial match)"),
    )
    has_phone = filters.BooleanFilter(
        method="filter_has_phone",
        help_text=_("Filter users who have a phone number"),
    )
    has_image = filters.BooleanFilter(
        method="filter_has_image",
        help_text=_("Filter users who have a profile image"),
    )
    has_birth_date = filters.BooleanFilter(
        method="filter_has_birth_date",
        help_text=_("Filter users who have a birth date"),
    )
    has_social_links = filters.BooleanFilter(
        method="filter_has_social_links",
        help_text=_("Filter users who have social media links"),
    )
    full_name = filters.CharFilter(
        method="filter_full_name",
        help_text=_("Filter by full name (first + last name)"),
    )

    def filter_has_phone(self, queryset, name, value):
        if value:
            return queryset.exclude(phone__isnull=True).exclude(phone="")
        else:
            return queryset.filter(phone__isnull=True) | queryset.filter(
                phone=""
            )

    def filter_has_image(self, queryset, name, value):
        if value:
            return queryset.exclude(image__isnull=True).exclude(image="")
        else:
            return queryset.filter(image__isnull=True) | queryset.filter(
                image=""
            )

    def filter_has_birth_date(self, queryset, name, value):
        if value:
            return queryset.exclude(birth_date__isnull=True)
        else:
            return queryset.filter(birth_date__isnull=True)

    def filter_has_social_links(self, queryset, name, value):
        from django.db.models import Q

        social_fields = (
            Q(twitter__gt="")
            | Q(linkedin__gt="")
            | Q(facebook__gt="")
            | Q(instagram__gt="")
            | Q(website__gt="")
            | Q(youtube__gt="")
            | Q(github__gt="")
        )

        if value:
            return queryset.filter(social_fields)
        else:
            return queryset.exclude(social_fields)

    def filter_full_name(self, queryset, name, value):
        from django.contrib.postgres.search import SearchVector

        if value:
            return queryset.annotate(
                full_name_search=SearchVector("first_name", "last_name")
            ).filter(full_name_search=value)
        return queryset

    class Meta:
        model = UserAccount
        fields = {
            "id": ["exact"],
            "email": ["exact", "icontains"],
            "username": ["exact", "icontains"],
            "first_name": ["icontains"],
            "last_name": ["icontains"],
            "is_active": ["exact"],
            "is_staff": ["exact"],
            "country": ["exact"],
            "region": ["exact"],
            "city": ["icontains"],
            "zipcode": ["icontains"],
            "created_at": ["gte", "lte"],
            "updated_at": ["gte", "lte"],
            "uuid": ["exact"],
        }
