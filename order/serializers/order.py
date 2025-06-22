from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from drf_spectacular.utils import extend_schema_field
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.utils.email import is_disposable_domain
from country.models import Country
from order.models.history import OrderHistory
from order.models.item import OrderItem
from order.models.order import Order
from order.serializers.item import (
    OrderItemCreateSerializer,
    OrderItemSerializer,
)
from order.signals import order_created
from pay_way.models import PayWay
from product.models.product import Product
from region.models import Region


class OrderSerializer(serializers.ModelSerializer[Order]):
    items = OrderItemSerializer(many=True)
    country = PrimaryKeyRelatedField(queryset=Country.objects.all())
    region = PrimaryKeyRelatedField(queryset=Region.objects.all())
    pay_way = PrimaryKeyRelatedField(queryset=PayWay.objects.all())
    paid_amount = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    shipping_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
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
            "id",
            "uuid",
            "paid_amount",
            "shipping_price",
            "total_price_items",
            "total_price_extra",
            "created_at",
            "updated_at",
            "status_updated_at",
            "can_be_canceled",
            "is_paid",
        )


class OrderDetailSerializer(OrderSerializer):
    order_timeline = serializers.SerializerMethodField(
        help_text="Order status timeline and history"
    )
    pricing_breakdown = serializers.SerializerMethodField(
        help_text="Detailed pricing breakdown"
    )
    tracking_details = serializers.SerializerMethodField(
        help_text="Tracking and shipping details"
    )
    phone = PhoneNumberField(read_only=True)
    mobile_phone = PhoneNumberField(read_only=True)

    @extend_schema_field(
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "change_type": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "description": {"type": "string"},
                    "user": {"type": "string", "nullable": True},
                    "previous_value": {"type": "object", "nullable": True},
                    "new_value": {"type": "object", "nullable": True},
                },
            },
        }
    )
    def get_order_timeline(self, obj):
        timeline = []

        timeline.append(
            {
                "change_type": "CREATED",
                "timestamp": obj.created_at,
                "description": "Order was created",
                "user": None,
                "previous_value": None,
                "new_value": None,
            }
        )

        history_records = OrderHistory.objects.filter(order=obj).order_by(
            "created_at"
        )

        for history in history_records:
            timeline.append(
                {
                    "change_type": history.change_type,
                    "timestamp": history.created_at,
                    "description": history.description,
                    "user": history.user.get_full_name()
                    if history.user
                    else None,
                    "previous_value": history.previous_value,
                    "new_value": history.new_value,
                }
            )

        return timeline

    @extend_schema_field(
        {
            "type": "object",
            "properties": {
                "items_subtotal": {"type": "number"},
                "shipping_cost": {"type": "number"},
                "extras_total": {"type": "number"},
                "grand_total": {"type": "number"},
                "currency": {"type": "string"},
                "paid_amount": {"type": "number"},
                "remaining_amount": {"type": "number"},
            },
        }
    )
    def get_pricing_breakdown(self, obj) -> dict:
        items_total = (
            obj.total_price_items.amount if obj.total_price_items else 0
        )
        shipping_total = obj.shipping_price.amount if obj.shipping_price else 0
        extras_total = (
            obj.total_price_extra.amount if obj.total_price_extra else 0
        )
        grand_total = items_total + shipping_total + extras_total

        return {
            "items_subtotal": items_total,
            "shipping_cost": shipping_total,
            "extras_total": extras_total,
            "grand_total": grand_total,
            "currency": obj.total_price_items.currency.code
            if obj.total_price_items
            else "EUR",
            "paid_amount": obj.paid_amount.amount if obj.paid_amount else 0,
            "remaining_amount": max(
                grand_total
                - (obj.paid_amount.amount if obj.paid_amount else 0),
                0,
            ),
        }

    @extend_schema_field(
        {
            "type": "object",
            "properties": {
                "tracking_number": {"type": "string"},
                "shipping_carrier": {"type": "string"},
                "has_tracking": {"type": "boolean"},
                "estimated_delivery": {"type": "string"},
                "tracking_url": {"type": "string"},
            },
        }
    )
    def get_tracking_details(self, obj) -> dict:
        return {
            "tracking_number": obj.tracking_number,
            "shipping_carrier": obj.shipping_carrier,
            "has_tracking": bool(obj.tracking_number),
            "estimated_delivery": None,  # @TODO - Would be calculated based on shipping method
            "tracking_url": f"https://track.carrier.com/{obj.tracking_number}"
            if obj.tracking_number
            else None,
        }

    class Meta(OrderSerializer.Meta):
        fields = (
            *OrderSerializer.Meta.fields,
            "items",
            "country",
            "region",
            "pay_way",
            "order_timeline",
            "pricing_breakdown",
            "tracking_details",
            "phone",
            "mobile_phone",
            "document_type",
            "payment_id",
            "payment_status",
            "payment_method",
            "tracking_number",
            "shipping_carrier",
            "customer_full_name",
            "is_completed",
            "is_canceled",
            "full_address",
        )
        read_only_fields = (
            *OrderSerializer.Meta.read_only_fields,
            "order_timeline",
            "pricing_breakdown",
            "tracking_details",
        )


class OrderWriteSerializer(serializers.ModelSerializer[Order]):
    items = OrderItemCreateSerializer(many=True)
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

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError(
                _("At least one item is required.")
            )

        for item_data in value:
            if item_data.get("quantity", 0) <= 0:
                raise serializers.ValidationError(
                    _("Item quantity must be greater than zero.")
                )

        return value

    def validate_email(self, value):
        if not value:
            raise serializers.ValidationError(_("Email is required."))

        email_domain = value.split("@")[-1]
        if is_disposable_domain(email_domain):
            raise serializers.ValidationError(
                _("Try using a different email address.")
            )
        return value

    def validate(self, data):
        items_data = data.get("items", [])
        for item_data in items_data:
            product_id = item_data.get("product")
            quantity = item_data.get("quantity", 0)

            try:
                if hasattr(product_id, "id"):
                    product = product_id
                else:
                    product = Product.objects.get(id=product_id)

                if not product.active:
                    raise serializers.ValidationError(
                        _(
                            "Product with id '{product_name}' is not available."
                        ).format(
                            product_name=product.safe_translation_getter(
                                "name", any_language=True
                            )
                        )
                    )

                if product.stock < quantity:
                    raise serializers.ValidationError(
                        _(
                            "Not enough stock for '{product_name}'. Available: {product_stock}, Requested: {quantity}"
                        ).format(
                            product_name=product.safe_translation_getter(
                                "name", any_language=True
                            ),
                            product_stock=product.stock,
                            quantity=quantity,
                        )
                    )
            except Product.DoesNotExist:
                raise serializers.ValidationError(
                    _("Product with id '{product_id}' does not exist.").format(
                        product_id=product_id
                    )
                ) from None

        return data

    def create(self, validated_data):
        items_data = validated_data.pop("items")

        order = Order.objects.create(**validated_data)

        for item_data in items_data:
            product = item_data.get("product")
            item_to_create = item_data.copy()
            item_to_create["price"] = product.price
            OrderItem.objects.create(order=order, **item_to_create)

        order.paid_amount = order.calculate_order_total_amount()
        order.save(update_fields=["paid_amount"])

        order_created.send(sender=Order, order=order)

        return order

    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()

            for item_data in items_data:
                product = item_data.get("product")
                item_to_create = item_data.copy()
                item_to_create["price"] = product.price
                OrderItem.objects.create(order=instance, **item_to_create)

        return instance

    class Meta:
        model = Order
        fields = (
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
            "total_price_items",
            "total_price_extra",
            "document_type",
            "payment_id",
            "payment_status",
            "payment_method",
            "tracking_number",
            "shipping_carrier",
        )
