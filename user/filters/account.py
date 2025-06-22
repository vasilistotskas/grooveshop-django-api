from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from user.models.account import UserAccount


class UserAccountFilter(filters.FilterSet):
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
    created_after = filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
        help_text=_("Filter users created after this date"),
    )
    created_before = filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="lte",
        help_text=_("Filter users created before this date"),
    )
    updated_after = filters.DateTimeFilter(
        field_name="updated_at",
        lookup_expr="gte",
        help_text=_("Filter users updated after this date"),
    )
    updated_before = filters.DateTimeFilter(
        field_name="updated_at",
        lookup_expr="lte",
        help_text=_("Filter users updated before this date"),
    )

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
        }
