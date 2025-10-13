from datetime import timedelta
from django.db.models import Q, F
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from cart.models import CartItem
from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin


class CartItemFilter(UUIDFilterMixin, CamelCaseTimeStampFilterSet):
    cart = filters.NumberFilter(
        field_name="cart__id",
        lookup_expr="exact",
        help_text=_("Filter by cart ID"),
    )
    cart__uuid = filters.UUIDFilter(
        field_name="cart__uuid",
        help_text=_("Filter by cart UUID"),
    )
    cart__user = filters.NumberFilter(
        field_name="cart__user__id",
        lookup_expr="exact",
        help_text=_("Filter by cart user ID"),
    )
    cart__user__email = filters.CharFilter(
        field_name="cart__user__email",
        lookup_expr="icontains",
        help_text=_("Filter by cart user email (partial match)"),
    )
    cart__user__name = filters.CharFilter(
        method="filter_cart_user_name",
        help_text=_("Filter by cart user name (first or last)"),
    )
    cart__is_guest = filters.BooleanFilter(
        method="filter_cart_is_guest",
        help_text=_("Filter items in guest carts"),
    )

    product = filters.NumberFilter(
        field_name="product__id",
        lookup_expr="exact",
        help_text=_("Filter by product ID"),
    )
    product__uuid = filters.UUIDFilter(
        field_name="product__uuid",
        help_text=_("Filter by product UUID"),
    )
    product__sku = filters.CharFilter(
        field_name="product__sku",
        lookup_expr="iexact",
        help_text=_("Filter by product sku (exact match)"),
    )
    product__name = filters.CharFilter(
        field_name="product__translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by product name (partial match)"),
    )
    product__category = filters.NumberFilter(
        field_name="product__category__id",
        help_text=_("Filter by product category ID"),
    )
    product__category__slug = filters.CharFilter(
        field_name="product__category__slug",
        help_text=_("Filter by product category slug"),
    )
    product__active = filters.BooleanFilter(
        field_name="product__active",
        help_text=_("Filter by product active status"),
    )

    quantity = filters.NumberFilter(
        field_name="quantity",
        help_text=_("Filter by exact quantity"),
    )
    min_quantity = filters.NumberFilter(
        field_name="quantity",
        lookup_expr="gte",
        help_text=_("Filter by minimum quantity"),
    )
    max_quantity = filters.NumberFilter(
        field_name="quantity",
        lookup_expr="lte",
        help_text=_("Filter by maximum quantity"),
    )

    min_price = filters.NumberFilter(
        field_name="product__price",
        lookup_expr="gte",
        help_text=_("Filter by minimum product price"),
    )
    max_price = filters.NumberFilter(
        field_name="product__price",
        lookup_expr="lte",
        help_text=_("Filter by maximum product price"),
    )
    min_total_price = filters.NumberFilter(
        method="filter_min_total_price",
        help_text=_("Filter by minimum total price (quantity * price)"),
    )
    max_total_price = filters.NumberFilter(
        method="filter_max_total_price",
        help_text=_("Filter by maximum total price (quantity * price)"),
    )

    with_discounts = filters.BooleanFilter(
        method="filter_with_discounts",
        help_text=_("Filter items with product discounts"),
    )
    min_discount_percent = filters.NumberFilter(
        field_name="product__discount_percent",
        lookup_expr="gte",
        help_text=_("Filter by minimum discount percentage"),
    )
    max_discount_percent = filters.NumberFilter(
        field_name="product__discount_percent",
        lookup_expr="lte",
        help_text=_("Filter by maximum discount percentage"),
    )

    in_active_carts = filters.BooleanFilter(
        method="filter_active_carts",
        help_text=_("Filter items in active carts (24hr)"),
    )
    in_abandoned_carts = filters.BooleanFilter(
        method="filter_abandoned_carts",
        help_text=_("Filter items in abandoned carts (30+ days)"),
    )
    cart_last_activity_after = filters.DateTimeFilter(
        field_name="cart__last_activity",
        lookup_expr="gte",
        help_text=_("Filter by cart last activity after date"),
    )
    cart_last_activity_before = filters.DateTimeFilter(
        field_name="cart__last_activity",
        lookup_expr="lte",
        help_text=_("Filter by cart last activity before date"),
    )

    class Meta:
        model = CartItem
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
            "id": ["exact", "in"],
            "quantity": ["exact", "gte", "lte"],
            "cart": ["exact"],
            "cart__user": ["exact"],
            "product": ["exact"],
            "product__active": ["exact"],
        }

    def filter_cart_user_name(self, queryset, name, value):
        """Filter by cart user's first or last name."""
        if value:
            return queryset.filter(
                Q(cart__user__first_name__icontains=value)
                | Q(cart__user__last_name__icontains=value)
            )
        return queryset

    def filter_cart_is_guest(self, queryset, name, value):
        """Filter items in guest (no user) carts."""
        if value is True:
            return queryset.filter(cart__user__isnull=True)
        elif value is False:
            return queryset.filter(cart__user__isnull=False)
        return queryset

    def filter_min_total_price(self, queryset, name, value):
        """Filter by minimum total price (quantity * price)."""
        if value is not None:
            return queryset.annotate(
                total_price=F("quantity") * F("product__price")
            ).filter(total_price__gte=value)
        return queryset

    def filter_max_total_price(self, queryset, name, value):
        """Filter by maximum total price (quantity * price)."""
        if value is not None:
            return queryset.annotate(
                total_price=F("quantity") * F("product__price")
            ).filter(total_price__lte=value)
        return queryset

    def filter_with_discounts(self, queryset, name, value):
        """Filter items with product discounts."""
        if value is True:
            return queryset.filter(product__discount_percent__gt=0)
        elif value is False:
            return queryset.filter(
                Q(product__discount_percent=0)
                | Q(product__discount_percent__isnull=True)
            )
        return queryset

    def filter_active_carts(self, queryset, name, value):
        """Filter items in active carts (activity within 24 hours)."""
        cutoff_time = timezone.now() - timedelta(hours=24)
        if value is True:
            return queryset.filter(cart__last_activity__gte=cutoff_time)
        elif value is False:
            return queryset.filter(cart__last_activity__lt=cutoff_time)
        return queryset

    def filter_abandoned_carts(self, queryset, name, value):
        """Filter items in abandoned carts (inactive 30+ days)."""
        cutoff_time = timezone.now() - timedelta(days=30)
        if value is True:
            return queryset.filter(cart__last_activity__lt=cutoff_time)
        elif value is False:
            return queryset.filter(cart__last_activity__gte=cutoff_time)
        return queryset
