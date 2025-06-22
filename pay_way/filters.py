from django.db import models
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from pay_way.models import PayWay


class PayWayFilter(filters.FilterSet):
    id = filters.NumberFilter(
        field_name="id",
        lookup_expr="exact",
        help_text=_("Filter by payment method ID"),
    )
    active = filters.BooleanFilter(
        field_name="active",
        help_text=_("Filter by active status"),
    )
    cost_min = filters.NumberFilter(
        field_name="cost",
        lookup_expr="gte",
        help_text=_("Filter by minimum cost"),
    )
    cost_max = filters.NumberFilter(
        field_name="cost",
        lookup_expr="lte",
        help_text=_("Filter by maximum cost"),
    )
    free_threshold_min = filters.NumberFilter(
        field_name="free_threshold",
        lookup_expr="gte",
        help_text=_("Filter by minimum free threshold"),
    )
    free_threshold_max = filters.NumberFilter(
        field_name="free_threshold",
        lookup_expr="lte",
        help_text=_("Filter by maximum free threshold"),
    )
    provider_code = filters.CharFilter(
        field_name="provider_code",
        lookup_expr="icontains",
        help_text=_("Filter by provider code (partial match)"),
    )
    is_online_payment = filters.BooleanFilter(
        field_name="is_online_payment",
        help_text=_("Filter by online payment status"),
    )
    requires_confirmation = filters.BooleanFilter(
        field_name="requires_confirmation",
        help_text=_("Filter by confirmation requirement"),
    )
    name = filters.CharFilter(
        field_name="translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by name (partial match)"),
    )
    description = filters.CharFilter(
        field_name="translations__description",
        lookup_expr="icontains",
        help_text=_("Filter by description (partial match)"),
    )
    has_icon = filters.BooleanFilter(
        method="filter_has_icon",
        help_text=_("Filter payment methods that have/don't have an icon"),
    )
    has_configuration = filters.BooleanFilter(
        method="filter_has_configuration",
        help_text=_(
            "Filter payment methods that have/don't have configuration"
        ),
    )

    class Meta:
        model = PayWay
        fields = [
            "id",
            "active",
            "cost_min",
            "cost_max",
            "free_threshold_min",
            "free_threshold_max",
            "provider_code",
            "is_online_payment",
            "requires_confirmation",
            "name",
            "description",
            "has_icon",
            "has_configuration",
        ]

    def filter_has_icon(self, queryset, name, value):
        if value is True:
            return queryset.exclude(
                models.Q(icon__isnull=True) | models.Q(icon__exact="")
            )
        elif value is False:
            return queryset.filter(
                models.Q(icon__isnull=True) | models.Q(icon__exact="")
            )
        return queryset

    def filter_has_configuration(self, queryset, name, value):
        if value is True:
            return queryset.exclude(configuration__isnull=True)
        elif value is False:
            return queryset.filter(configuration__isnull=True)
        return queryset
