from django.db import models
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from core.filters.camel_case_filters import CamelCaseFilterMixin
from core.filters.core import (
    TimeStampFilterMixin,
    UUIDFilterMixin,
    SortableFilterMixin,
)
from pay_way.models import PayWay


class PayWayFilter(
    TimeStampFilterMixin,
    UUIDFilterMixin,
    SortableFilterMixin,
    CamelCaseFilterMixin,
    filters.FilterSet,
):
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
    shipping_provider_code = filters.CharFilter(
        method="filter_carrier_compat",
        help_text=_(
            "Filter pay ways compatible with the given shipping carrier. "
            "Each carrier owns its own compatibility rules — BoxNow "
            "(``boxnow``) rejects COD on locker pickup; other carriers "
            "pass through unchanged. Pair with ``shippingKind``."
        ),
    )
    shipping_kind = filters.CharFilter(
        method="filter_carrier_compat",
        help_text=_(
            "Pair with ``shippingProviderCode`` to filter pay ways by "
            "the carrier's compatibility rules for that kind."
        ),
    )

    class Meta:
        model = PayWay
        fields = {
            "id": ["exact"],
            "active": ["exact"],
            "cost": ["gte", "lte"],
            "free_threshold": ["gte", "lte"],
            "provider_code": ["icontains"],
            "is_online_payment": ["exact"],
            "requires_confirmation": ["exact"],
            "sort_order": ["exact", "gte", "lte"],
            "uuid": ["exact"],
        }

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

    def filter_carrier_compat(self, queryset, name, value):
        """Both ``shippingProviderCode`` and ``shippingKind`` route to
        this method; we only filter when BOTH are provided so the API
        stays predictable. ``self.data`` lets us read the sibling
        param without binding the methods together at the field level.
        """
        from pay_way.services import PayWayService

        # ``self.data`` is already camel-decoded by CamelCaseFilterMixin
        # so we read snake_case keys.
        provider_code = self.data.get("shipping_provider_code")
        shipping_kind = self.data.get("shipping_kind")
        if not provider_code or not shipping_kind:
            return queryset
        return PayWayService.filter_by_carrier(
            queryset,
            provider_code=provider_code,
            shipping_kind=shipping_kind,
        )
