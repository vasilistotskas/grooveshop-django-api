from datetime import timedelta
from django.db.models import Count, Sum, Q, F, DecimalField
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from cart.models import Cart
from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin


class CartFilter(UUIDFilterMixin, CamelCaseTimeStampFilterSet):
    user = filters.NumberFilter(
        field_name="user__id",
        lookup_expr="exact",
        help_text=_("Filter by user ID"),
    )
    user__isnull = filters.BooleanFilter(
        field_name="user",
        lookup_expr="isnull",
        help_text=_("Filter carts with/without users"),
    )
    user_email = filters.CharFilter(
        field_name="user__email",
        lookup_expr="icontains",
        help_text=_("Filter by user email (partial match)"),
    )
    user_name = filters.CharFilter(
        method="filter_user_name",
        help_text=_("Filter by user full name (first or last name)"),
    )
    user__is_active = filters.BooleanFilter(
        field_name="user__is_active",
        help_text=_("Filter by active users"),
    )

    is_guest = filters.BooleanFilter(
        method="filter_is_guest",
        help_text=_("Filter guest carts (True) or user carts (False)"),
    )
    cart_type = filters.ChoiceFilter(
        method="filter_cart_type",
        choices=[
            ("user", "User Cart"),
            ("guest", "Guest Cart"),
            ("anonymous", "Anonymous Cart"),
        ],
        help_text=_("Filter by cart type"),
    )

    last_activity = filters.DateTimeFilter(
        field_name="last_activity",
        help_text=_("Filter by exact last activity date"),
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
    is_active = filters.BooleanFilter(
        method="filter_is_active",
        help_text=_(
            "Filter active/abandoned carts (based on 30-day inactivity)"
        ),
    )
    is_abandoned = filters.BooleanFilter(
        method="filter_is_abandoned",
        help_text=_("Filter abandoned carts (inactive for 30+ days)"),
    )
    days_inactive = filters.NumberFilter(
        method="filter_days_inactive",
        help_text=_("Filter carts inactive for at least X days"),
    )

    has_items = filters.BooleanFilter(
        method="filter_has_items",
        help_text=_("Filter carts that have/don't have items"),
    )
    min_items = filters.NumberFilter(
        method="filter_min_items",
        help_text=_("Filter carts with at least X total items (quantity)"),
    )
    max_items = filters.NumberFilter(
        method="filter_max_items",
        help_text=_("Filter carts with at most X total items (quantity)"),
    )
    min_unique_items = filters.NumberFilter(
        method="filter_min_unique_items",
        help_text=_("Filter carts with at least X unique items"),
    )
    max_unique_items = filters.NumberFilter(
        method="filter_max_unique_items",
        help_text=_("Filter carts with at most X unique items"),
    )

    min_total_value = filters.NumberFilter(
        method="filter_min_total_value",
        help_text=_("Filter carts with total value at least X"),
    )
    max_total_value = filters.NumberFilter(
        method="filter_max_total_value",
        help_text=_("Filter carts with total value at most X"),
    )
    has_discounts = filters.BooleanFilter(
        method="filter_has_discounts",
        help_text=_("Filter carts with/without discounted items"),
    )

    class Meta:
        model = Cart
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
            "id": ["exact", "in"],
            "user": ["exact", "isnull"],
            "last_activity": ["exact", "gte", "lte", "date"],
        }

    def filter_user_name(self, queryset, name, value):
        """Filter by user's first or last name."""
        if value:
            return queryset.filter(
                Q(user__first_name__icontains=value)
                | Q(user__last_name__icontains=value)
            )
        return queryset

    def filter_is_guest(self, queryset, name, value):
        """Filter guest (no user) vs user carts."""
        if value is True:
            return queryset.filter(user__isnull=True)
        elif value is False:
            return queryset.filter(user__isnull=False)
        return queryset

    def filter_cart_type(self, queryset, name, value):
        """Filter by specific cart type."""
        if value == "user":
            return queryset.filter(user__isnull=False)
        elif value == "guest":
            return queryset.filter(user__isnull=True)
        elif value == "anonymous":
            return queryset.filter(user__isnull=True)
        return queryset

    def filter_is_active(self, queryset, name, value):
        """Filter active carts (activity within 30 days)."""
        cutoff = timezone.now() - timedelta(days=30)
        if value is True:
            return queryset.filter(last_activity__gte=cutoff)
        elif value is False:
            return queryset.filter(last_activity__lt=cutoff)
        return queryset

    def filter_is_abandoned(self, queryset, name, value):
        """Filter abandoned carts (inactive 30+ days)."""
        cutoff = timezone.now() - timedelta(days=30)
        if value is True:
            return queryset.filter(last_activity__lt=cutoff)
        elif value is False:
            return queryset.filter(last_activity__gte=cutoff)
        return queryset

    def filter_days_inactive(self, queryset, name, value):
        """Filter carts inactive for at least X days."""
        if value is not None:
            cutoff = timezone.now() - timedelta(days=value)
            return queryset.filter(last_activity__lte=cutoff)
        return queryset

    def filter_has_items(self, queryset, name, value):
        """Filter carts based on item presence."""
        if value is True:
            return queryset.annotate(item_count=Count("items")).filter(
                item_count__gt=0
            )
        elif value is False:
            return queryset.annotate(item_count=Count("items")).filter(
                item_count=0
            )
        return queryset

    def filter_min_items(self, queryset, name, value):
        """Filter carts with minimum total quantity."""
        if value is not None:
            return queryset.annotate(
                total_quantity=Coalesce(Sum("items__quantity"), 0)
            ).filter(total_quantity__gte=value)
        return queryset

    def filter_max_items(self, queryset, name, value):
        """Filter carts with maximum total quantity."""
        if value is not None:
            return queryset.annotate(
                total_quantity=Coalesce(Sum("items__quantity"), 0)
            ).filter(total_quantity__lte=value)
        return queryset

    def filter_min_unique_items(self, queryset, name, value):
        """Filter carts with minimum unique items."""
        if value is not None:
            return queryset.annotate(
                unique_items=Count("items", distinct=True)
            ).filter(unique_items__gte=value)
        return queryset

    def filter_max_unique_items(self, queryset, name, value):
        """Filter carts with maximum unique items."""
        if value is not None:
            return queryset.annotate(
                unique_items=Count("items", distinct=True)
            ).filter(unique_items__lte=value)
        return queryset

    def filter_min_total_value(self, queryset, name, value):
        """Filter carts with minimum total value."""
        if value is not None:
            return queryset.annotate(
                total_value=Coalesce(
                    Sum(
                        F("items__quantity") * F("items__product__price"),
                        output_field=DecimalField(),
                    ),
                    0,
                    output_field=DecimalField(),
                )
            ).filter(total_value__gte=value)
        return queryset

    def filter_max_total_value(self, queryset, name, value):
        """Filter carts with maximum total value."""
        if value is not None:
            return queryset.annotate(
                total_value=Coalesce(
                    Sum(
                        F("items__quantity") * F("items__product__price"),
                        output_field=DecimalField(),
                    ),
                    0,
                    output_field=DecimalField(),
                )
            ).filter(total_value__lte=value)
        return queryset

    def filter_has_discounts(self, queryset, name, value):
        """Filter carts with discounted items."""
        if value is True:
            return queryset.filter(items__discount_value__gt=0).distinct()
        elif value is False:
            return queryset.exclude(items__discount_value__gt=0).distinct()
        return queryset
