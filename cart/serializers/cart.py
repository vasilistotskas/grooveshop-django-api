from django.conf import settings
from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from drf_spectacular.helpers import lazy_serializer
from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from cart.models import Cart
from cart.serializers.item import CartItemSerializer
from product.serializers.product import ProductSerializer


class CartWriteSerializer(serializers.ModelSerializer[Cart]):
    class Meta:
        model = Cart
        fields = ("user",)


class CartSerializer(serializers.ModelSerializer[Cart]):
    items = CartItemSerializer(many=True, read_only=True)
    total_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    total_discount_value = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )
    total_vat_value = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )
    total_weight_grams = serializers.IntegerField(
        read_only=True,
        help_text=_(
            "Total cart weight in grams. Forwarded to "
            "/api/v1/shipping/options at checkout so ACS live pricing "
            "quotes against the actual weight bracket the voucher "
            "mint will charge."
        ),
    )
    currency = serializers.SerializerMethodField(
        help_text=_(
            "ISO 4217 currency code for all monetary values in this cart"
        ),
    )

    @extend_schema_field(OpenApiTypes.STR)
    def get_currency(self, obj: Cart) -> str:
        return str(settings.DEFAULT_CURRENCY)

    class Meta:
        model = Cart
        fields = (
            "id",
            "user",
            "uuid",
            "items",
            "total_price",
            "total_discount_value",
            "total_vat_value",
            "total_items",
            "total_items_unique",
            "total_weight_grams",
            "currency",
            "created_at",
            "updated_at",
            "last_activity",
        )
        read_only_fields = (
            "id",
            "uuid",
            "total_price",
            "total_discount_value",
            "total_vat_value",
            "total_items",
            "total_items_unique",
            "total_weight_grams",
            "created_at",
            "updated_at",
            "last_activity",
        )


class CartDetailSerializer(CartSerializer):
    recommendations = serializers.SerializerMethodField(
        help_text=_("Product recommendations based on cart contents")
    )

    @extend_schema_field(
        lazy_serializer("product.serializers.product.ProductSerializer")(
            many=True
        )
    )
    def get_recommendations(self, obj: Cart):
        categories = set()
        for item in obj.items.all():
            if item.product.category:
                categories.add(item.product.category)

        if categories:
            from product.models.product import Product  # noqa: PLC0415

            recommendations = (
                Product.objects.filter(category__in=categories, active=True)
                .exclude(id__in=obj.items.values_list("product_id", flat=True))
                .order_by("-view_count")[:4]
            )

            return ProductSerializer(
                recommendations, many=True, context=self.context
            ).data
        return []

    class Meta(CartSerializer.Meta):
        fields = (
            *CartSerializer.Meta.fields,
            "recommendations",
        )


class ReleaseReservationsRequestSerializer(serializers.Serializer):
    """Serializer for releasing stock reservations."""

    reservation_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=True,
        help_text=_("List of reservation IDs to release"),
    )


class ReleaseReservationsResponseSerializer(serializers.Serializer):
    """Serializer for release reservations response."""

    message = serializers.CharField(
        help_text=_("Success message"),
    )
    released_count = serializers.IntegerField(
        help_text=_("Number of reservations released"),
    )
    failed_releases = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        help_text=_("List of failed releases with error details"),
    )


class ReserveStockResponseSerializer(serializers.Serializer):
    """Serializer for reserve stock response."""

    reservation_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text=_("List of created stock reservation IDs"),
    )
    message = serializers.CharField(
        help_text=_("Success message"),
    )


class CartCreatePaymentIntentRequestSerializer(serializers.Serializer):
    """Request body for ``POST /api/v1/cart/create-payment-intent``.

    The shipping fields are required so the PaymentIntent amount uses
    the same per-carrier shipping calculation the order-create
    verification step runs.  Without them the view falls back to the
    generic ``FREE_SHIPPING_THRESHOLD`` / ``CHECKOUT_SHIPPING_PRICE``
    pair which silently disagrees with the carrier adapters whenever
    the per-carrier thresholds differ — producing a
    ``PaymentAmountMismatchError`` at order-create time.
    """

    pay_way_id = serializers.IntegerField(
        min_value=1,
        help_text=_("ID of the selected PayWay (must be online Stripe)."),
    )
    shipping_provider_code = serializers.CharField(
        max_length=32,
        help_text=_(
            "Carrier code matching a registered shipping adapter "
            "(e.g. 'acs', 'boxnow'). Used to compute shipping cost "
            "with the same per-carrier free-shipping threshold the "
            "order-create path will apply."
        ),
    )
    shipping_kind = serializers.ChoiceField(
        # Choices declared inline to avoid the circular import that
        # would happen if ``shipping.enum`` were pulled in at module
        # import time (cart -> shipping -> order -> cart).
        choices=(
            ("home_delivery", "home_delivery"),
            ("pickup_point", "pickup_point"),
        ),
        help_text=_(
            "Fulfilment kind for the carrier (home_delivery or "
            "pickup_point). Required so the per-kind feature flags "
            "(e.g. ACS_SMARTPOINT_ENABLED) and BoxNow's PICKUP_POINT "
            "gate are honoured."
        ),
    )
    country_id = serializers.CharField(
        max_length=2,
        required=False,
        allow_blank=True,
        help_text=_(
            "Optional ISO 3166-1 alpha-2 country code — drives the "
            "country-level shipping multiplier. Match what the "
            "order-create body will carry."
        ),
    )
    region_id = serializers.CharField(
        max_length=16,
        required=False,
        allow_blank=True,
        help_text=_(
            "Optional region code — drives the region-level shipping "
            "adjustment."
        ),
    )


class CartPaymentIntentResponseSerializer(serializers.Serializer):
    """Response body returned by the create-payment-intent cart action."""

    client_secret = serializers.CharField(
        help_text=_(
            "Stripe PaymentIntent client secret for frontend confirmation"
        ),
    )
    payment_intent_id = serializers.CharField(
        help_text=_("Stripe PaymentIntent ID to be stored on the order"),
    )
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text=_("Total charge amount (cart + shipping + payment fee)"),
    )
    currency = serializers.CharField(
        max_length=3,
        help_text=_("ISO 4217 currency code (e.g. EUR)"),
    )
