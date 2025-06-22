from django.db.models import Count, Sum
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from cart.models import Cart


class CartFilter(filters.FilterSet):
    id = filters.NumberFilter(
        field_name="id",
        lookup_expr="exact",
        help_text=_("Filter by cart ID"),
    )
    user = filters.NumberFilter(
        field_name="user__id",
        lookup_expr="exact",
        help_text=_("Filter by user ID"),
    )
    user_email = filters.CharFilter(
        field_name="user__email",
        lookup_expr="icontains",
        help_text=_("Filter by user email (partial match)"),
    )
    session_key = filters.CharFilter(
        field_name="session_key",
        lookup_expr="exact",
        help_text=_("Filter by session key"),
    )

    is_guest = filters.BooleanFilter(
        method="filter_is_guest",
        help_text=_("Filter guest carts (True) or user carts (False)"),
    )
    has_items = filters.BooleanFilter(
        method="filter_has_items",
        help_text=_("Filter carts that have/don't have items"),
    )
    is_active = filters.BooleanFilter(
        method="filter_is_active",
        help_text=_("Filter active/abandoned carts"),
    )

    min_items = filters.NumberFilter(
        method="filter_min_items",
        help_text=_("Filter carts with at least X items"),
    )
    max_items = filters.NumberFilter(
        method="filter_max_items",
        help_text=_("Filter carts with at most X items"),
    )

    last_activity_after = filters.DateTimeFilter(
        field_name="last_activity",
        lookup_expr="gte",
        help_text=_("Filter carts with last activity after this date"),
    )
    last_activity_before = filters.DateTimeFilter(
        field_name="last_activity",
        lookup_expr="lte",
        help_text=_("Filter carts with last activity before this date"),
    )

    class Meta:
        model = Cart
        fields = [
            "id",
            "user",
            "user_email",
            "session_key",
            "is_guest",
            "has_items",
            "is_active",
            "min_items",
            "max_items",
            "last_activity_after",
            "last_activity_before",
        ]

    def filter_is_guest(self, queryset, name, value):
        if value is True:
            return queryset.filter(user__isnull=True)
        elif value is False:
            return queryset.filter(user__isnull=False)
        return queryset

    def filter_has_items(self, queryset, name, value):
        if value is True:
            return queryset.annotate(item_count=Count("items")).filter(
                item_count__gt=0
            )
        elif value is False:
            return queryset.annotate(item_count=Count("items")).filter(
                item_count=0
            )
        return queryset

    def filter_is_active(self, queryset, name, value):
        if value is True:
            return queryset.active()
        elif value is False:
            return queryset.abandoned()
        return queryset

    def filter_min_items(self, queryset, name, value):
        if value is not None:
            return queryset.annotate(
                total_quantity=Sum("items__quantity")
            ).filter(total_quantity__gte=value)
        return queryset

    def filter_max_items(self, queryset, name, value):
        if value is not None:
            return queryset.annotate(
                total_quantity=Sum("items__quantity")
            ).filter(total_quantity__lte=value)
        return queryset
