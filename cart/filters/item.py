from datetime import timedelta

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from cart.models import CartItem


class CartItemFilter(filters.FilterSet):
    id = filters.NumberFilter(
        field_name="id",
        lookup_expr="exact",
        help_text=_("Filter by cart item ID"),
    )
    cart = filters.NumberFilter(
        field_name="cart__id",
        lookup_expr="exact",
        help_text=_("Filter by cart ID"),
    )
    product = filters.NumberFilter(
        field_name="product__id",
        lookup_expr="exact",
        help_text=_("Filter by product ID"),
    )
    product_name = filters.CharFilter(
        field_name="product__translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by product name (partial match)"),
    )
    user = filters.NumberFilter(
        field_name="cart__user__id",
        lookup_expr="exact",
        help_text=_("Filter by user ID"),
    )
    user_email = filters.CharFilter(
        field_name="cart__user__email",
        lookup_expr="icontains",
        help_text=_("Filter by user email (partial match)"),
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
        field_name="product__price__amount",
        lookup_expr="gte",
        help_text=_("Filter by minimum product price"),
    )
    max_price = filters.NumberFilter(
        field_name="product__price__amount",
        lookup_expr="lte",
        help_text=_("Filter by maximum product price"),
    )

    in_active_carts = filters.BooleanFilter(
        method="filter_active_carts",
        help_text=_("Filter items in active carts"),
    )

    with_discounts = filters.BooleanFilter(
        method="filter_with_discounts",
        help_text=_("Filter items with product discounts"),
    )

    def filter_active_carts(self, queryset, name, value):
        if value:
            cutoff_time = timezone.now() - timedelta(hours=24)
            return queryset.filter(cart__last_activity__gte=cutoff_time)
        else:
            cutoff_time = timezone.now() - timedelta(hours=24)
            return queryset.filter(cart__last_activity__lt=cutoff_time)

    def filter_with_discounts(self, queryset, name, value):
        if value:
            return queryset.filter(product__discount_percent__gt=0)
        else:
            return queryset.filter(product__discount_percent=0)

    class Meta:
        model = CartItem
        fields = [
            "id",
            "cart",
            "product",
            "product_name",
            "user",
            "user_email",
            "min_quantity",
            "max_quantity",
            "min_price",
            "max_price",
            "in_active_carts",
            "with_discounts",
        ]
