import django_filters
from django.utils.translation import gettext_lazy as _

from order.models.item import OrderItem
from order.models.order import Order


class OrderFilter(django_filters.FilterSet):
    id = django_filters.NumberFilter(field_name="id")
    user = django_filters.NumberFilter(field_name="user")
    user_id = django_filters.NumberFilter(field_name="user_id")
    country = django_filters.NumberFilter(field_name="country")
    region = django_filters.NumberFilter(field_name="region")
    pay_way = django_filters.NumberFilter(field_name="pay_way")
    status = django_filters.CharFilter(field_name="status")
    payment_status = django_filters.CharFilter(field_name="payment_status")
    document_type = django_filters.CharFilter(field_name="document_type")
    floor = django_filters.CharFilter(field_name="floor")
    location_type = django_filters.CharFilter(field_name="location_type")

    created_after = django_filters.DateTimeFilter(
        field_name="created_at", lookup_expr="gte"
    )
    created_before = django_filters.DateTimeFilter(
        field_name="created_at", lookup_expr="lte"
    )
    status_updated_after = django_filters.DateTimeFilter(
        field_name="status_updated_at", lookup_expr="gte"
    )
    status_updated_before = django_filters.DateTimeFilter(
        field_name="status_updated_at", lookup_expr="lte"
    )

    paid_amount_min = django_filters.NumberFilter(
        field_name="paid_amount", lookup_expr="gte"
    )
    paid_amount_max = django_filters.NumberFilter(
        field_name="paid_amount", lookup_expr="lte"
    )
    shipping_price_min = django_filters.NumberFilter(
        field_name="shipping_price", lookup_expr="gte"
    )
    shipping_price_max = django_filters.NumberFilter(
        field_name="shipping_price", lookup_expr="lte"
    )

    has_user = django_filters.BooleanFilter(method="filter_has_user")
    has_tracking = django_filters.BooleanFilter(method="filter_has_tracking")
    has_payment_id = django_filters.BooleanFilter(
        method="filter_has_payment_id"
    )
    has_mobile_phone = django_filters.BooleanFilter(
        method="filter_has_mobile_phone"
    )
    has_customer_notes = django_filters.BooleanFilter(
        method="filter_has_customer_notes"
    )

    city = django_filters.CharFilter(field_name="city", lookup_expr="icontains")
    zipcode = django_filters.CharFilter(field_name="zipcode")
    place = django_filters.CharFilter(
        field_name="place", lookup_expr="icontains"
    )

    class Meta:
        model = Order
        fields = [
            "id",
            "user",
            "user_id",
            "country",
            "region",
            "pay_way",
            "status",
            "payment_status",
            "document_type",
            "floor",
            "location_type",
            "city",
            "zipcode",
            "place",
        ]

    def filter_has_user(self, queryset, name, value):
        if value:
            return queryset.filter(user__isnull=False)
        return queryset.filter(user__isnull=True)

    def filter_has_tracking(self, queryset, name, value):
        if value:
            return queryset.exclude(tracking_number="")
        return queryset.filter(tracking_number="")

    def filter_has_payment_id(self, queryset, name, value):
        if value:
            return queryset.exclude(payment_id="")
        return queryset.filter(payment_id="")

    def filter_has_mobile_phone(self, queryset, name, value):
        if value:
            return queryset.filter(mobile_phone__isnull=False)
        return queryset.filter(mobile_phone__isnull=True)

    def filter_has_customer_notes(self, queryset, name, value):
        if value:
            return queryset.exclude(customer_notes="")
        return queryset.filter(customer_notes="")


class OrderItemFilter(django_filters.FilterSet):
    id = django_filters.NumberFilter(
        field_name="id",
        lookup_expr="exact",
        help_text=_("Filter by order item ID"),
    )
    order = django_filters.NumberFilter(
        field_name="order__id",
        lookup_expr="exact",
        help_text=_("Filter by order ID"),
    )
    product = django_filters.NumberFilter(
        field_name="product__id",
        lookup_expr="exact",
        help_text=_("Filter by product ID"),
    )
    product_name = django_filters.CharFilter(
        field_name="product__translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by product name (partial match)"),
    )

    price_min = django_filters.NumberFilter(
        field_name="price",
        lookup_expr="gte",
        help_text=_("Filter by minimum price"),
    )
    price_max = django_filters.NumberFilter(
        field_name="price",
        lookup_expr="lte",
        help_text=_("Filter by maximum price"),
    )

    quantity_min = django_filters.NumberFilter(
        field_name="quantity",
        lookup_expr="gte",
        help_text=_("Filter by minimum quantity"),
    )
    quantity_max = django_filters.NumberFilter(
        field_name="quantity",
        lookup_expr="lte",
        help_text=_("Filter by maximum quantity"),
    )

    is_refunded = django_filters.BooleanFilter(
        field_name="is_refunded",
        help_text=_("Filter by refund status"),
    )
    has_notes = django_filters.BooleanFilter(
        method="filter_has_notes",
        help_text=_("Filter items that have/don't have notes"),
    )

    created_after = django_filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
        help_text=_("Filter items created after this date"),
    )
    created_before = django_filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="lte",
        help_text=_("Filter items created before this date"),
    )

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "order",
            "product",
            "product_name",
            "price_min",
            "price_max",
            "quantity_min",
            "quantity_max",
            "is_refunded",
            "has_notes",
            "created_after",
            "created_before",
        ]

    def filter_has_notes(self, queryset, name, value):
        if value is True:
            return queryset.exclude(notes="")
        elif value is False:
            return queryset.filter(notes="")
        return queryset
