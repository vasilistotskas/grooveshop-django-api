from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from country.serializers import CountrySerializer
from order.models import Order
from order.models import OrderItem
from pay_way.serializers import PayWaySerializer
from product.serializers.product import ProductSerializer
from region.serializers import RegionSerializer


class OrderItemSerializer(serializers.ModelSerializer):
    product = ProductSerializer()

    class Meta:
        model = OrderItem
        fields = (
            "id",
            "price",
            "product",
            "quantity",
            "created_at",
            "updated_at",
            "uuid",
            "sort_order",
            "total_price",
        )


class OrderSerializer(serializers.ModelSerializer):
    order_item_order = OrderItemSerializer(many=True)
    country = serializers.SerializerMethodField("get_country")
    region = serializers.SerializerMethodField("get_region")
    pay_way = serializers.SerializerMethodField("get_pay_way")

    @extend_schema_field(CountrySerializer)
    def get_country(self, order) -> CountrySerializer:
        return CountrySerializer(order.country).data

    @extend_schema_field(RegionSerializer)
    def get_region(self, order) -> RegionSerializer:
        return RegionSerializer(order.region).data

    @extend_schema_field(PayWaySerializer)
    def get_pay_way(self, order) -> PayWaySerializer:
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
            "order_item_order",
            "shipping_price",
            "document_type",
            "created_at",
            "updated_at",
            "uuid",
            "total_price",
            "full_address",
        )

    def create(self, validated_data):
        items_data = validated_data.pop("order_item_order")
        order = Order.objects.create(**validated_data)

        for item_data in items_data:
            OrderItem.objects.create(order=order, **item_data)

        return order
