from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from drf_spectacular.utils import extend_schema_field
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers

from country.serializers import CountrySerializer
from order.models.item import OrderItem
from order.models.order import Order
from order.serializers.item import (
    OrderItemCreateUpdateSerializer,
    OrderItemSerializer,
)
from order.signals import order_created
from pay_way.serializers import PayWaySerializer
from product.models.product import Product
from region.serializers import RegionSerializer


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)
    country = serializers.SerializerMethodField("get_country")
    region = serializers.SerializerMethodField("get_region")
    pay_way = serializers.SerializerMethodField("get_pay_way")
    paid_amount = MoneyField(max_digits=11, decimal_places=2, required=False)
    shipping_price = MoneyField(max_digits=11, decimal_places=2)
    total_price_items = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )
    total_price_extra = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )
    phone = PhoneNumberField()
    mobile_phone = PhoneNumberField(required=False)
    status_display = serializers.SerializerMethodField("get_status_display")
    can_be_canceled = serializers.BooleanField(read_only=True)
    is_paid = serializers.BooleanField(read_only=True)

    @extend_schema_field(CountrySerializer)
    def get_country(self, order: Order):
        return CountrySerializer(order.country).data

    @extend_schema_field(RegionSerializer)
    def get_region(self, order: Order):
        return RegionSerializer(order.region).data

    @extend_schema_field(PayWaySerializer)
    def get_pay_way(self, order: Order):
        return PayWaySerializer(order.pay_way).data

    def get_status_display(self, order: Order) -> str:
        return order.get_status_display()

    class Meta:
        model = Order
        fields = (
            "id",
            "user",
            "country",
            "region",
            "floor",
            "location_type",
            "street",
            "street_number",
            "pay_way",
            "status",
            "status_display",
            "status_updated_at",
            "first_name",
            "last_name",
            "email",
            "zipcode",
            "place",
            "city",
            "phone",
            "mobile_phone",
            "customer_notes",
            "paid_amount",
            "items",
            "shipping_price",
            "document_type",
            "created_at",
            "updated_at",
            "uuid",
            "total_price_items",
            "total_price_extra",
            "full_address",
            "payment_id",
            "payment_status",
            "payment_method",
            "can_be_canceled",
            "is_paid",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "total_price_items",
            "total_price_extra",
            "full_address",
            "status_updated_at",
            "payment_id",
            "payment_status",
            "payment_method",
            "status_display",
            "can_be_canceled",
            "is_paid",
        )


class OrderDetailSerializer(OrderSerializer):
    tracking_info = serializers.SerializerMethodField("get_tracking_info")
    time_since_order = serializers.SerializerMethodField("get_time_since_order")

    def get_tracking_info(self, order: Order) -> dict | None:
        if order.tracking_number and order.shipping_carrier:
            return {
                "tracking_number": order.tracking_number,
                "shipping_carrier": order.shipping_carrier,
            }
        return None

    def get_time_since_order(self, order: Order) -> str:
        if not order.created_at:
            return "Unknown"

        delta = timezone.now() - order.created_at
        minutes = delta.seconds // 60
        hours = minutes // 60
        minutes = minutes % 60

        if hours > 0:
            return f"{hours} hours, {minutes} minutes"
        else:
            return f"{minutes} minutes"

    class Meta(OrderSerializer.Meta):
        fields = (
            *OrderSerializer.Meta.fields,
            "tracking_info",
            "tracking_number",
            "shipping_carrier",
            "time_since_order",
            "customer_full_name",
            "is_completed",
            "is_canceled",
        )
        read_only_fields = (
            *OrderSerializer.Meta.read_only_fields,
            "tracking_info",
            "tracking_number",
            "shipping_carrier",
            "time_since_order",
            "customer_full_name",
            "is_completed",
            "is_canceled",
        )


class OrderCreateUpdateSerializer(serializers.ModelSerializer):
    items = OrderItemCreateUpdateSerializer(many=True)
    paid_amount = MoneyField(max_digits=11, decimal_places=2, required=False)
    shipping_price = MoneyField(max_digits=11, decimal_places=2)
    total_price_items = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )
    total_price_extra = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )
    phone = PhoneNumberField()
    mobile_phone = PhoneNumberField(required=False)
    payment_id = serializers.CharField(required=False)
    payment_status = serializers.CharField(required=False)
    payment_method = serializers.CharField(required=False)

    class Meta:
        model = Order
        fields = (
            "id",
            "user",
            "country",
            "region",
            "floor",
            "location_type",
            "street",
            "street_number",
            "pay_way",
            "status",
            "first_name",
            "last_name",
            "email",
            "zipcode",
            "place",
            "city",
            "phone",
            "mobile_phone",
            "paid_amount",
            "customer_notes",
            "items",
            "shipping_price",
            "document_type",
            "created_at",
            "updated_at",
            "uuid",
            "total_price_items",
            "total_price_extra",
            "full_address",
            "payment_id",
            "payment_status",
            "payment_method",
            "tracking_number",
            "shipping_carrier",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "total_price_items",
            "total_price_extra",
            "full_address",
            "status_updated_at",
        )

    def to_internal_value(self, data):
        if (
            isinstance(data.get("shipping_price"), dict)
            and "amount" in data["shipping_price"]
        ):
            data = data.copy()
            data["shipping_price"] = data["shipping_price"]["amount"]

        return super().to_internal_value(data)

    def validate(self, data):
        super().validate(data)

        items_data = data.get("items", [])

        for item_data in items_data:
            product = item_data["product"]
            quantity = item_data["quantity"]

            if product.stock < quantity:
                raise serializers.ValidationError(
                    {
                        "items": [
                            {
                                "quantity": _(
                                    "Not enough stock available for {product_name}. Available: {product_stock}"
                                ).format(
                                    product_name=product.name,
                                    product_stock=product.stock,
                                )
                            }
                        ]
                    }
                )

        if "shipping_price" in data and data["shipping_price"] is not None:
            shipping_currency = data["shipping_price"].currency

            for item_data in items_data:
                product = item_data["product"]
                if product.price.currency != shipping_currency:
                    raise serializers.ValidationError(
                        {
                            "shipping_price": _(
                                "Currency mismatch: shipping is in {shipping_currency} but product {product_name} is in {product_currency}"
                            ).format(
                                shipping_currency=shipping_currency,
                                product_name=product.name,
                                product_currency=product.price.currency,
                            )
                        }
                    )

        return data

    def create(self, validated_data):
        items_data = validated_data.pop("items")

        try:
            if "paid_amount" in validated_data and not hasattr(
                validated_data["paid_amount"], "currency"
            ):
                from django.conf import settings
                from djmoney.money import Money

                validated_data["paid_amount"] = Money(
                    validated_data["paid_amount"], settings.DEFAULT_CURRENCY
                )

            if "shipping_price" in validated_data and not hasattr(
                validated_data["shipping_price"], "currency"
            ):
                from django.conf import settings
                from djmoney.money import Money

                validated_data["shipping_price"] = Money(
                    validated_data["shipping_price"], settings.DEFAULT_CURRENCY
                )

            order = Order.objects.create(**validated_data)

            for item_data in items_data:
                product = item_data.get("product")
                item_to_create = item_data.copy()
                item_to_create["price"] = product.price
                OrderItem.objects.create(order=order, **item_to_create)

            order.paid_amount = order.calculate_order_total_amount()
            order.save(update_fields=["paid_amount"])

            order_created.send(sender=Order, order=order)

        except Product.DoesNotExist as err:
            raise serializers.ValidationError(
                _("One or more products do not exist.")
            ) from err

        return order

    def update(self, instance, validated_data):
        if "items" in validated_data:
            items_data = validated_data.pop("items")

            instance.items.all().delete()

            for item_data in items_data:
                product = item_data.get("product")
                item_to_create = item_data.copy()
                item_to_create["price"] = product.price
                OrderItem.objects.create(order=instance, **item_to_create)

        return super().update(instance, validated_data)
