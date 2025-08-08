from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin
from order.models.item import OrderItem
from order.models.order import Order
from order.enum.status import OrderStatus, PaymentStatus


class OrderFilter(UUIDFilterMixin, CamelCaseTimeStampFilterSet):
    user = filters.NumberFilter(
        field_name="user__id", help_text=_("Filter by user ID")
    )
    country = filters.CharFilter(
        field_name="country__alpha_2", help_text=_("Filter by country code")
    )
    region = filters.CharFilter(
        field_name="region__alpha", help_text=_("Filter by region code")
    )
    pay_way = filters.NumberFilter(
        field_name="pay_way__id", help_text=_("Filter by payment method ID")
    )
    status = filters.ChoiceFilter(
        field_name="status",
        choices=OrderStatus.choices,
        help_text=_("Filter by order status"),
    )
    payment_status = filters.ChoiceFilter(
        field_name="payment_status",
        choices=PaymentStatus.choices,
        help_text=_("Filter by payment status"),
    )
    document_type = filters.CharFilter(
        field_name="document_type", help_text=_("Filter by document type")
    )
    floor = filters.CharFilter(
        field_name="floor", help_text=_("Filter by floor")
    )
    location_type = filters.CharFilter(
        field_name="location_type", help_text=_("Filter by location type")
    )

    user__email = filters.CharFilter(
        field_name="user__email",
        lookup_expr="icontains",
        help_text=_("Filter by user email (case-insensitive)"),
    )
    user__first_name = filters.CharFilter(
        field_name="user__first_name",
        lookup_expr="icontains",
        help_text=_("Filter by user first name (case-insensitive)"),
    )
    user__last_name = filters.CharFilter(
        field_name="user__last_name",
        lookup_expr="icontains",
        help_text=_("Filter by user last name (case-insensitive)"),
    )
    user__is_active = filters.BooleanFilter(
        field_name="user__is_active",
        help_text=_("Filter by user active status"),
    )

    first_name = filters.CharFilter(
        field_name="first_name",
        lookup_expr="icontains",
        help_text=_("Filter by customer first name (case-insensitive)"),
    )
    last_name = filters.CharFilter(
        field_name="last_name",
        lookup_expr="icontains",
        help_text=_("Filter by customer last name (case-insensitive)"),
    )
    email = filters.CharFilter(
        field_name="email",
        lookup_expr="icontains",
        help_text=_("Filter by customer email (case-insensitive)"),
    )
    phone = filters.CharFilter(
        field_name="phone",
        lookup_expr="icontains",
        help_text=_("Filter by phone number"),
    )
    mobile_phone = filters.CharFilter(
        field_name="mobile_phone",
        lookup_expr="icontains",
        help_text=_("Filter by mobile phone number"),
    )

    city = filters.CharFilter(
        field_name="city",
        lookup_expr="icontains",
        help_text=_("Filter by city (case-insensitive)"),
    )
    zipcode = filters.CharFilter(
        field_name="zipcode", help_text=_("Filter by zipcode")
    )
    place = filters.CharFilter(
        field_name="place",
        lookup_expr="icontains",
        help_text=_("Filter by place (case-insensitive)"),
    )
    street = filters.CharFilter(
        field_name="street",
        lookup_expr="icontains",
        help_text=_("Filter by street (case-insensitive)"),
    )
    street_number = filters.CharFilter(
        field_name="street_number",
        lookup_expr="icontains",
        help_text=_("Filter by street number"),
    )

    country__name = filters.CharFilter(
        field_name="country__translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by country name (case-insensitive)"),
    )
    country__alpha_2 = filters.CharFilter(
        field_name="country__alpha_2",
        help_text=_("Filter by country alpha-2 code"),
    )
    region__name = filters.CharFilter(
        field_name="region__translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by region name (case-insensitive)"),
    )

    pay_way__name = filters.CharFilter(
        field_name="pay_way__translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by payment method name (case-insensitive)"),
    )
    pay_way__is_online_payment = filters.BooleanFilter(
        field_name="pay_way__is_online_payment",
        help_text=_("Filter by online payment methods"),
    )

    status_updated_after = filters.DateTimeFilter(
        field_name="status_updated_at",
        lookup_expr="gte",
        help_text=_("Filter orders with status updated after this date"),
    )
    status_updated_before = filters.DateTimeFilter(
        field_name="status_updated_at",
        lookup_expr="lte",
        help_text=_("Filter orders with status updated before this date"),
    )
    has_status_updated_at = filters.BooleanFilter(
        method="filter_has_status_updated_at",
        help_text=_("Filter orders that have status update timestamp"),
    )

    paid_amount_min = filters.NumberFilter(
        field_name="paid_amount",
        lookup_expr="gte",
        help_text=_("Filter by minimum paid amount"),
    )
    paid_amount_max = filters.NumberFilter(
        field_name="paid_amount",
        lookup_expr="lte",
        help_text=_("Filter by maximum paid amount"),
    )
    shipping_price_min = filters.NumberFilter(
        field_name="shipping_price",
        lookup_expr="gte",
        help_text=_("Filter by minimum shipping price"),
    )
    shipping_price_max = filters.NumberFilter(
        field_name="shipping_price",
        lookup_expr="lte",
        help_text=_("Filter by maximum shipping price"),
    )

    has_user = filters.BooleanFilter(
        method="filter_has_user",
        help_text=_("Filter orders that have/don't have a user"),
    )
    has_tracking = filters.BooleanFilter(
        method="filter_has_tracking",
        help_text=_("Filter orders that have/don't have tracking number"),
    )
    has_payment_id = filters.BooleanFilter(
        method="filter_has_payment_id",
        help_text=_("Filter orders that have/don't have payment ID"),
    )
    has_mobile_phone = filters.BooleanFilter(
        method="filter_has_mobile_phone",
        help_text=_("Filter orders that have/don't have mobile phone"),
    )
    has_customer_notes = filters.BooleanFilter(
        method="filter_has_customer_notes",
        help_text=_("Filter orders that have/don't have customer notes"),
    )
    is_paid = filters.BooleanFilter(
        method="filter_is_paid", help_text=_("Filter paid/unpaid orders")
    )
    is_completed = filters.BooleanFilter(
        method="filter_is_completed", help_text=_("Filter completed orders")
    )
    is_canceled = filters.BooleanFilter(
        method="filter_is_canceled", help_text=_("Filter canceled orders")
    )
    can_be_canceled = filters.BooleanFilter(
        method="filter_can_be_canceled",
        help_text=_("Filter orders that can be canceled"),
    )

    active_orders = filters.BooleanFilter(
        method="filter_active_orders",
        help_text=_(
            "Filter active orders (pending, processing, shipped, delivered)"
        ),
    )
    final_orders = filters.BooleanFilter(
        method="filter_final_orders",
        help_text=_("Filter final orders (completed, canceled, refunded)"),
    )
    recent_orders = filters.BooleanFilter(
        method="filter_recent_orders",
        help_text=_("Filter orders from the last 30 days"),
    )
    needs_processing = filters.BooleanFilter(
        method="filter_needs_processing",
        help_text=_("Filter orders that need processing"),
    )

    tracking_number = filters.CharFilter(
        field_name="tracking_number",
        lookup_expr="icontains",
        help_text=_("Filter by tracking number"),
    )
    payment_id = filters.CharFilter(
        field_name="payment_id",
        lookup_expr="icontains",
        help_text=_("Filter by payment ID"),
    )
    payment_method = filters.CharFilter(
        field_name="payment_method",
        lookup_expr="icontains",
        help_text=_("Filter by payment method"),
    )
    shipping_carrier = filters.CharFilter(
        field_name="shipping_carrier",
        lookup_expr="icontains",
        help_text=_("Filter by shipping carrier"),
    )

    user_ids = filters.CharFilter(
        method="filter_user_ids",
        help_text=_("Filter by multiple user IDs (comma-separated)"),
    )
    status_list = filters.CharFilter(
        method="filter_status_list",
        help_text=_("Filter by multiple statuses (comma-separated)"),
    )
    country_ids = filters.CharFilter(
        method="filter_country_ids",
        help_text=_("Filter by multiple country IDs (comma-separated)"),
    )

    class Meta:
        model = Order
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
            "id": ["exact", "in"],
            "status": ["exact", "in"],
            "payment_status": ["exact", "in"],
            "document_type": ["exact"],
            "floor": ["exact"],
            "location_type": ["exact"],
            "city": ["exact", "icontains"],
            "zipcode": ["exact"],
            "place": ["exact", "icontains"],
            "street": ["exact", "icontains"],
            "street_number": ["exact", "icontains"],
            "first_name": ["exact", "icontains"],
            "last_name": ["exact", "icontains"],
            "email": ["exact", "icontains"],
            "phone": ["exact", "icontains"],
            "mobile_phone": ["exact", "icontains"],
            "customer_notes": ["exact", "icontains"],
            "tracking_number": ["exact", "icontains"],
            "payment_id": ["exact", "icontains"],
            "payment_method": ["exact", "icontains"],
            "shipping_carrier": ["exact", "icontains"],
            "paid_amount": ["gte", "lte"],
            "shipping_price": ["gte", "lte"],
            "status_updated_at": ["gte", "lte", "date"],
            "user": ["exact"],
            "country": ["exact"],
            "region": ["exact"],
            "pay_way": ["exact"],
        }

    def filter_has_user(self, queryset, name, value):
        if value is True:
            return queryset.filter(user__isnull=False)
        elif value is False:
            return queryset.filter(user__isnull=True)
        return queryset

    def filter_has_tracking(self, queryset, name, value):
        if value is True:
            return queryset.exclude(tracking_number="")
        elif value is False:
            return queryset.filter(tracking_number="")
        return queryset

    def filter_has_payment_id(self, queryset, name, value):
        if value is True:
            return queryset.exclude(payment_id="")
        elif value is False:
            return queryset.filter(payment_id="")
        return queryset

    def filter_has_mobile_phone(self, queryset, name, value):
        if value is True:
            return queryset.filter(mobile_phone__isnull=False)
        elif value is False:
            return queryset.filter(mobile_phone__isnull=True)
        return queryset

    def filter_has_customer_notes(self, queryset, name, value):
        if value is True:
            return queryset.exclude(customer_notes="")
        elif value is False:
            return queryset.filter(customer_notes="")
        return queryset

    def filter_has_status_updated_at(self, queryset, name, value):
        if value is True:
            return queryset.filter(status_updated_at__isnull=False)
        elif value is False:
            return queryset.filter(status_updated_at__isnull=True)
        return queryset

    def filter_is_paid(self, queryset, name, value):
        if value is True:
            return queryset.filter(
                Q(payment_status=PaymentStatus.COMPLETED) | Q(paid_amount__gt=0)
            )
        elif value is False:
            return queryset.filter(
                Q(
                    payment_status__in=[
                        PaymentStatus.PENDING,
                        PaymentStatus.FAILED,
                    ]
                )
                & Q(paid_amount__lte=0)
            )
        return queryset

    def filter_is_completed(self, queryset, name, value):
        if value is True:
            return queryset.filter(status=OrderStatus.COMPLETED)
        elif value is False:
            return queryset.exclude(status=OrderStatus.COMPLETED)
        return queryset

    def filter_is_canceled(self, queryset, name, value):
        if value is True:
            return queryset.filter(status=OrderStatus.CANCELED)
        elif value is False:
            return queryset.exclude(status=OrderStatus.CANCELED)
        return queryset

    def filter_can_be_canceled(self, queryset, name, value):
        cancellable_statuses = [OrderStatus.PENDING, OrderStatus.PROCESSING]
        if value is True:
            return queryset.filter(status__in=cancellable_statuses)
        elif value is False:
            return queryset.exclude(status__in=cancellable_statuses)
        return queryset

    def filter_active_orders(self, queryset, name, value):
        if value is True:
            return queryset.filter(status__in=OrderStatus.get_active_statuses())
        return queryset

    def filter_final_orders(self, queryset, name, value):
        if value is True:
            return queryset.filter(status__in=OrderStatus.get_final_statuses())
        return queryset

    def filter_recent_orders(self, queryset, name, value):
        if value is True:
            from django.utils import timezone
            from datetime import timedelta

            thirty_days_ago = timezone.now() - timedelta(days=30)
            return queryset.filter(created_at__gte=thirty_days_ago)
        return queryset

    def filter_needs_processing(self, queryset, name, value):
        if value is True:
            return queryset.filter(
                status__in=[OrderStatus.PENDING, OrderStatus.PROCESSING]
            )
        return queryset

    def filter_user_ids(self, queryset, name, value):
        if value:
            try:
                user_ids = [
                    int(id.strip()) for id in value.split(",") if id.strip()
                ]
                return queryset.filter(user__id__in=user_ids)
            except ValueError:
                return queryset.none()
        return queryset

    def filter_status_list(self, queryset, name, value):
        if value:
            statuses = [
                status.strip() for status in value.split(",") if status.strip()
            ]
            return queryset.filter(status__in=statuses)
        return queryset

    def filter_country_ids(self, queryset, name, value):
        if value:
            country_codes = [
                code.strip() for code in value.split(",") if code.strip()
            ]
            return queryset.filter(country__alpha_2__in=country_codes)
        return queryset


class OrderItemFilter(UUIDFilterMixin, CamelCaseTimeStampFilterSet):
    order = filters.NumberFilter(
        field_name="order__id", help_text=_("Filter by order ID")
    )
    product = filters.NumberFilter(
        field_name="product__id", help_text=_("Filter by product ID")
    )
    is_refunded = filters.BooleanFilter(
        field_name="is_refunded", help_text=_("Filter by refund status")
    )

    product__name = filters.CharFilter(
        field_name="product__translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by product name (case-insensitive)"),
    )
    product__sku = filters.CharFilter(
        field_name="product__sku",
        lookup_expr="icontains",
        help_text=_("Filter by product SKU"),
    )
    product__category = filters.NumberFilter(
        field_name="product__category__id",
        help_text=_("Filter by product category ID"),
    )
    product__category__name = filters.CharFilter(
        field_name="product__category__translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by product category name (case-insensitive)"),
    )
    product__active = filters.BooleanFilter(
        field_name="product__active",
        help_text=_("Filter by product active status"),
    )

    order__status = filters.ChoiceFilter(
        field_name="order__status",
        choices=OrderStatus.choices,
        help_text=_("Filter by order status"),
    )
    order__payment_status = filters.ChoiceFilter(
        field_name="order__payment_status",
        choices=PaymentStatus.choices,
        help_text=_("Filter by order payment status"),
    )
    order__user = filters.NumberFilter(
        field_name="order__user__id", help_text=_("Filter by order user ID")
    )
    order__user__email = filters.CharFilter(
        field_name="order__user__email",
        lookup_expr="icontains",
        help_text=_("Filter by order user email (case-insensitive)"),
    )
    order__first_name = filters.CharFilter(
        field_name="order__first_name",
        lookup_expr="icontains",
        help_text=_("Filter by order customer first name (case-insensitive)"),
    )
    order__last_name = filters.CharFilter(
        field_name="order__last_name",
        lookup_expr="icontains",
        help_text=_("Filter by order customer last name (case-insensitive)"),
    )
    order__email = filters.CharFilter(
        field_name="order__email",
        lookup_expr="icontains",
        help_text=_("Filter by order customer email (case-insensitive)"),
    )
    order__country = filters.CharFilter(
        field_name="order__country__alpha_2",
        help_text=_("Filter by order country code"),
    )
    order__region = filters.CharFilter(
        field_name="order__region__alpha",
        help_text=_("Filter by order region code"),
    )

    price_min = filters.NumberFilter(
        field_name="price",
        lookup_expr="gte",
        help_text=_("Filter by minimum price"),
    )
    price_max = filters.NumberFilter(
        field_name="price",
        lookup_expr="lte",
        help_text=_("Filter by maximum price"),
    )
    price_exact = filters.NumberFilter(
        field_name="price",
        lookup_expr="exact",
        help_text=_("Filter by exact price"),
    )

    quantity_min = filters.NumberFilter(
        field_name="quantity",
        lookup_expr="gte",
        help_text=_("Filter by minimum quantity"),
    )
    quantity_max = filters.NumberFilter(
        field_name="quantity",
        lookup_expr="lte",
        help_text=_("Filter by maximum quantity"),
    )
    quantity_exact = filters.NumberFilter(
        field_name="quantity",
        lookup_expr="exact",
        help_text=_("Filter by exact quantity"),
    )
    original_quantity_min = filters.NumberFilter(
        field_name="original_quantity",
        lookup_expr="gte",
        help_text=_("Filter by minimum original quantity"),
    )
    original_quantity_max = filters.NumberFilter(
        field_name="original_quantity",
        lookup_expr="lte",
        help_text=_("Filter by maximum original quantity"),
    )

    refunded_quantity_min = filters.NumberFilter(
        field_name="refunded_quantity",
        lookup_expr="gte",
        help_text=_("Filter by minimum refunded quantity"),
    )
    refunded_quantity_max = filters.NumberFilter(
        field_name="refunded_quantity",
        lookup_expr="lte",
        help_text=_("Filter by maximum refunded quantity"),
    )
    has_refunded_quantity = filters.BooleanFilter(
        method="filter_has_refunded_quantity",
        help_text=_("Filter items that have/don't have refunded quantity"),
    )
    is_partially_refunded = filters.BooleanFilter(
        method="filter_is_partially_refunded",
        help_text=_("Filter partially refunded items"),
    )
    is_fully_refunded = filters.BooleanFilter(
        method="filter_is_fully_refunded",
        help_text=_("Filter fully refunded items"),
    )

    has_notes = filters.BooleanFilter(
        method="filter_has_notes",
        help_text=_("Filter items that have/don't have notes"),
    )
    notes = filters.CharFilter(
        field_name="notes",
        lookup_expr="icontains",
        help_text=_("Filter by notes content (case-insensitive)"),
    )

    high_value_items = filters.BooleanFilter(
        method="filter_high_value_items",
        help_text=_("Filter high value items (price > 100)"),
    )
    bulk_items = filters.BooleanFilter(
        method="filter_bulk_items",
        help_text=_("Filter bulk items (quantity > 5)"),
    )
    recent_items = filters.BooleanFilter(
        method="filter_recent_items",
        help_text=_("Filter items from the last 7 days"),
    )

    order_ids = filters.CharFilter(
        method="filter_order_ids",
        help_text=_("Filter by multiple order IDs (comma-separated)"),
    )
    product_ids = filters.CharFilter(
        method="filter_product_ids",
        help_text=_("Filter by multiple product IDs (comma-separated)"),
    )
    order_statuses = filters.CharFilter(
        method="filter_order_statuses",
        help_text=_("Filter by multiple order statuses (comma-separated)"),
    )

    class Meta:
        model = OrderItem
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
            "id": ["exact", "in"],
            "price": ["exact", "gte", "lte"],
            "quantity": ["exact", "gte", "lte"],
            "original_quantity": ["exact", "gte", "lte"],
            "refunded_quantity": ["exact", "gte", "lte"],
            "is_refunded": ["exact"],
            "notes": ["exact", "icontains"],
            "sort_order": ["exact", "gte", "lte"],
            "order": ["exact"],
            "product": ["exact"],
        }

    def filter_has_notes(self, queryset, name, value):
        if value is True:
            return queryset.exclude(notes="")
        elif value is False:
            return queryset.filter(notes="")
        return queryset

    def filter_has_refunded_quantity(self, queryset, name, value):
        if value is True:
            return queryset.filter(refunded_quantity__gt=0)
        elif value is False:
            return queryset.filter(refunded_quantity=0)
        return queryset

    def filter_is_partially_refunded(self, queryset, name, value):
        if value is True:
            return queryset.filter(
                refunded_quantity__gt=0,
                refunded_quantity__lt=models.F("quantity"),
            )
        elif value is False:
            return queryset.exclude(
                refunded_quantity__gt=0,
                refunded_quantity__lt=models.F("quantity"),
            )
        return queryset

    def filter_is_fully_refunded(self, queryset, name, value):
        if value is True:
            return queryset.filter(is_refunded=True)
        elif value is False:
            return queryset.filter(is_refunded=False)
        return queryset

    def filter_high_value_items(self, queryset, name, value):
        if value is True:
            return queryset.filter(price__gt=100)
        elif value is False:
            return queryset.filter(price__lte=100)
        return queryset

    def filter_bulk_items(self, queryset, name, value):
        if value is True:
            return queryset.filter(quantity__gt=5)
        elif value is False:
            return queryset.filter(quantity__lte=5)
        return queryset

    def filter_recent_items(self, queryset, name, value):
        if value is True:
            from django.utils import timezone
            from datetime import timedelta

            seven_days_ago = timezone.now() - timedelta(days=7)
            return queryset.filter(created_at__gte=seven_days_ago)
        return queryset

    def filter_order_ids(self, queryset, name, value):
        if value:
            try:
                order_ids = [
                    int(id.strip()) for id in value.split(",") if id.strip()
                ]
                return queryset.filter(order__id__in=order_ids)
            except ValueError:
                return queryset.none()
        return queryset

    def filter_product_ids(self, queryset, name, value):
        if value:
            try:
                product_ids = [
                    int(id.strip()) for id in value.split(",") if id.strip()
                ]
                return queryset.filter(product__id__in=product_ids)
            except ValueError:
                return queryset.none()
        return queryset

    def filter_order_statuses(self, queryset, name, value):
        if value:
            statuses = [
                status.strip() for status in value.split(",") if status.strip()
            ]
            return queryset.filter(order__status__in=statuses)
        return queryset
