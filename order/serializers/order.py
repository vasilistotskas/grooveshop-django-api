from typing import override

from djmoney.contrib.django_rest_framework import MoneyField
from drf_spectacular.utils import extend_schema_field
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers

from core.api.serializers import BaseExpandSerializer
from country.serializers import CountrySerializer
from order.models.item import OrderItem
from order.models.order import Order
from order.serializers.item import CheckoutItemSerializer
from order.serializers.item import OrderItemCreateUpdateSerializer
from order.serializers.item import OrderItemSerializer
from order.signals import order_created
from pay_way.serializers import PayWaySerializer
from product.models.product import Product
from region.serializers import RegionSerializer


class OrderSerializer(BaseExpandSerializer):
    items = OrderItemSerializer(many=True)
    country = serializers.SerializerMethodField("get_country")
    region = serializers.SerializerMethodField("get_region")
    pay_way = serializers.SerializerMethodField("get_pay_way")
    paid_amount = MoneyField(max_digits=11, decimal_places=2, required=False)
    shipping_price = MoneyField(max_digits=11, decimal_places=2)
    total_price_items = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    total_price_extra = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    phone = PhoneNumberField()
    mobile_phone = PhoneNumberField(required=False)

    @extend_schema_field(CountrySerializer)
    def get_country(self, order):
        return CountrySerializer(order.country).data

    @extend_schema_field(RegionSerializer)
    def get_region(self, order):
        return RegionSerializer(order.region).data

    @extend_schema_field(PayWaySerializer)
    def get_pay_way(self, order):
        return PayWaySerializer(order.pay_way).data

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
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "total_price_items",
            "total_price_extra",
            "full_address",
        )


class OrderCreateUpdateSerializer(BaseExpandSerializer):
    items = OrderItemCreateUpdateSerializer(many=True)
    paid_amount = MoneyField(max_digits=11, decimal_places=2, required=False)
    shipping_price = MoneyField(max_digits=11, decimal_places=2)
    total_price_items = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    total_price_extra = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    phone = PhoneNumberField()
    mobile_phone = PhoneNumberField(required=False)

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
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "total_price_items",
            "total_price_extra",
            "full_address",
        )

    @override
    def validate(self, data):
        super().validate(data)

        items_data = data.get("items", [])

        for item_data in items_data:
            product = item_data["product"]
            quantity = item_data["quantity"]

            if product.stock < quantity:
                raise serializers.ValidationError(f"Product {product.name} does not have enough stock.")

        return data

    @override
    def create(self, validated_data):
        items_data = validated_data.pop("items")

        try:
            order = Order.objects.create(**validated_data)

            for item_data in items_data:
                product = item_data.get("product")

                item_data["price"] = product.final_price

                OrderItem.objects.create(order=order, **item_data)

            order_created.send(sender=Order, order=order)

        except Product.DoesNotExist:
            raise serializers.ValidationError("One or more products do not exist.")

        return order

    @override
    def update(self, instance, validated_data):
        if "items" in validated_data:
            items_data = validated_data.pop("items")

            instance.items.all().delete()

            for item_data in items_data:
                OrderItem.objects.create(order=instance, **item_data)

        return super().update(instance, validated_data)


class CheckoutSerializer(BaseExpandSerializer):
    items = CheckoutItemSerializer(many=True)
    paid_amount = MoneyField(max_digits=11, decimal_places=2, required=False)
    shipping_price = MoneyField(max_digits=11, decimal_places=2)
    total_price_items = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    total_price_extra = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    phone = PhoneNumberField()
    mobile_phone = PhoneNumberField(required=False)

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
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "total_price_items",
            "total_price_extra",
            "full_address",
        )

    @override
    def validate(self, data):
        super().validate(data)

        items_data = data.get("items", [])

        for item_data in items_data:
            product = item_data["product"]
            quantity = item_data["quantity"]

            if product.stock < quantity:
                raise serializers.ValidationError(f"Product {product.name} does not have enough stock.")

        return data

    @override
    def create(self, validated_data):
        items_data = validated_data.pop("items")

        try:
            order = Order.objects.create(**validated_data)

            for item_data in items_data:
                product = item_data.get("product")

                # Set the price of the product to the final price
                item_data["price"] = product.final_price

                OrderItem.objects.create(order=order, **item_data)

            order_created.send(sender=Order, order=order)

        except Product.DoesNotExist:
            raise serializers.ValidationError("One or more products do not exist.")

        return order
