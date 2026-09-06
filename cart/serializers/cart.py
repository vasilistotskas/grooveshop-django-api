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
        # ``user`` is intrinsic to the cart (set from the request at
        # creation; guest carts merge on login automatically). It must never
        # be client-writable, or an anonymous caller could attach a guest
        # cart to any account via update (mass-assignment IDOR).
        read_only_fields = ("user",)


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
    promotion_discount = serializers.SerializerMethodField(
        help_text=_(
            "Discount granted by live promotions (automatic + applied "
            "coupon), on top of any product markdown already inside "
            "the line prices"
        ),
    )
    promotion_free_shipping = serializers.SerializerMethodField(
        help_text=_("Whether a live promotion waives the shipping cost"),
    )
    applied_coupon_codes = serializers.SerializerMethodField(
        help_text=_("Coupon codes currently attached to this cart"),
    )
    promotion_gift_items = serializers.SerializerMethodField(
        help_text=_("Free-gift entitlements earned by this cart"),
    )
    promotion_near_miss = serializers.SerializerMethodField(
        help_text=_(
            "Automatic promotions blocked only by their minimum "
            "subtotal — 'add X more to unlock'"
        ),
    )
    b2b_pricing = serializers.SerializerMethodField(
        help_text=_(
            "Present when wholesale group pricing is applied to this "
            "cart's line prices; null for retail carts. Lets the "
            "storefront show a wholesale badge and hide the coupon "
            "input (promotions don't stack on B2B prices unless the "
            "merchant opts in)."
        ),
    )

    @extend_schema_field(OpenApiTypes.STR)
    def get_currency(self, obj: Cart) -> str:
        return str(settings.DEFAULT_CURRENCY)

    @extend_schema_field(
        {
            "type": "object",
            "nullable": True,
            "properties": {
                "applied": {"type": "boolean"},
                "groupName": {"type": "string"},
                "allowPromotions": {"type": "boolean"},
                "allowLoyalty": {"type": "boolean"},
                "minOrderValue": {"type": "string"},
                "belowMinimum": {"type": "boolean"},
            },
        }
    )
    def get_b2b_pricing(self, obj: Cart) -> dict | None:
        context = getattr(obj, "_b2b_pricing", None)
        if context is None:
            return None
        from b2b.services import B2BPricingService, B2BService

        return {
            "applied": True,
            "group_name": context.group.name,
            # Whether retail promotions stack on the wholesale prices —
            # the storefront hides the coupon input when they don't, so
            # shoppers never type codes the engine will silently ignore.
            "allow_promotions": B2BService.promotions_allowed(),
            # Whether this cart earns AND can redeem loyalty points.
            # The storefront hides both the points-earned promise and
            # the redemption widget when false — the backend drops a
            # redemption on these carts, so promising points here would
            # advertise a reward that is never granted.
            "allow_loyalty": B2BService.loyalty_allowed(),
            # Standard wholesale term: the sidebar warns (and checkout
            # refuses) below this items total. "0.00" disables it.
            "min_order_value": str(context.group.min_order_value.amount),
            "below_minimum": B2BPricingService.min_order_value_unmet(obj)
            is not None,
        }

    def _promotion_result(self, obj: Cart):
        # One engine evaluation per serialized cart — the three
        # promotion fields all read from it.
        cache = self.context.setdefault("_promotion_results", {})
        if obj.pk not in cache:
            from promotion.services import PromotionEngine

            cache[obj.pk] = PromotionEngine.evaluate(obj, user=obj.user)
        return cache[obj.pk]

    @extend_schema_field(OpenApiTypes.DECIMAL)
    def get_promotion_discount(self, obj: Cart):
        return self._promotion_result(obj).discount_total.amount

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_promotion_free_shipping(self, obj: Cart) -> bool:
        return self._promotion_result(obj).free_shipping

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_applied_coupon_codes(self, obj: Cart) -> list[str]:
        # `.all()`, not `.values_list()`: values_list builds a NEW
        # queryset and always hits the database, so it walks straight
        # past the `applied_codes__code` prefetch and costs a query per
        # cart on the staff list. Iterating the prefetched rows uses it.
        return [row.code.code for row in obj.applied_codes.all()]

    @extend_schema_field(
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "promotionId": {"type": "integer"},
                    "name": {"type": "string"},
                    "productId": {"type": "integer"},
                    "productName": {"type": "string"},
                    "productImagePath": {"type": "string"},
                    "quantity": {"type": "integer"},
                },
            },
            "description": (
                "FREE_GIFT entitlements the cart has earned — added to "
                "the order as zero-price lines at checkout."
            ),
        }
    )
    def get_promotion_gift_items(self, obj: Cart) -> list[dict]:
        # ``name`` is the PROMOTION name (why the gift exists);
        # ``productName``/``productImagePath`` describe the actual
        # product the shopper receives — the storefront leads with
        # those so the free line reads as a real item, not a slogan.
        return [
            {
                "promotionId": gift.promotion.id,
                "name": gift.promotion.safe_translation_getter(
                    "name", any_language=True
                )
                or "",
                "productId": gift.product.id,
                "productName": gift.product.safe_translation_getter(
                    "name", any_language=True
                )
                or "",
                "productImagePath": gift.product.main_image_path,
                "quantity": gift.quantity,
            }
            for gift in self._promotion_result(obj).gift_items
        ]

    @extend_schema_field(
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "promotionId": {"type": "integer"},
                    "name": {"type": "string"},
                    "remainingAmount": {"type": "number"},
                },
            },
            "description": (
                "Automatic promotions the cart ALMOST qualifies for — "
                "blocked only by their minimum subtotal. Powers the "
                "'add X more to unlock' teaser."
            ),
        }
    )
    def get_promotion_near_miss(self, obj: Cart) -> list[dict]:
        return [
            {
                "promotionId": entry.promotion.id,
                "name": entry.promotion.safe_translation_getter(
                    "name", any_language=True
                )
                or "",
                "remainingAmount": entry.remaining_amount,
            }
            for entry in self._promotion_result(obj).near_miss
        ]

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
            "promotion_discount",
            "promotion_free_shipping",
            "applied_coupon_codes",
            "promotion_gift_items",
            "promotion_near_miss",
            "b2b_pricing",
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
            "promotion_discount",
            "promotion_free_shipping",
            "applied_coupon_codes",
            "promotion_gift_items",
            "promotion_near_miss",
            "b2b_pricing",
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
            from product.models.product import Product

            # `for_list()` carries the prefetches ProductSerializer
            # needs — translations, main image, review and like counts.
            # A bare queryset here cost a query PER recommended product
            # for each of those, on the primary "view cart" response.
            # It already filters to active, non-deleted products.
            recommendations = (
                Product.objects.for_list()
                .filter(category__in=categories)
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

    ``shipping_kind`` is required so the view's shipping calculation
    follows the same code path the order-create verification runs.
    ``shipping_provider_code`` is required for ``pickup_point`` (the
    carrier identity drives the locker quote + per-carrier threshold)
    but **omitted for ``home_delivery``** — home delivery is
    provider-agnostic in checkout per the frontend's
    ``shared/shipping/index.ts::carrierForMethod`` contract, and the
    backend resolves the active home-delivery provider at order
    creation. Sending whatever the frontend has guarantees both calc
    paths agree.
    """

    pay_way_id = serializers.IntegerField(
        min_value=1,
        help_text=_("ID of the selected PayWay (must be online Stripe)."),
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
    shipping_provider_code = serializers.CharField(
        max_length=32,
        required=False,
        allow_blank=True,
        help_text=_(
            "Carrier code matching a registered shipping adapter "
            "(e.g. 'acs', 'boxnow'). Required for ``pickup_point``; "
            "omit/empty for ``home_delivery`` (the backend uses the "
            "generic flat rate, matching what the order-create "
            "verification will compute for the same body)."
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

    email = serializers.EmailField(
        required=False,
        allow_blank=True,
        help_text=_(
            "Checkout email. Lets promotion eligibility checks that "
            "depend on customer identity (first-order-only, "
            "per-customer limits) run against the same identity the "
            "order-create verification will use, keeping the "
            "PaymentIntent amount in lockstep."
        ),
    )
    gift_card_codes = serializers.ListField(
        child=serializers.CharField(max_length=32),
        required=False,
        allow_empty=True,
        max_length=3,
        help_text=_(
            "Gift card codes the shopper wants to redeem — the intent "
            "is created for the REMAINDER after their balances. Pass "
            "the same codes in the order-create body."
        ),
    )
    loyalty_points_to_redeem = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=0,
        help_text=_(
            "Loyalty points the shopper will redeem at order creation. "
            "The intent amount subtracts the resulting discount so the "
            "provider captures what the customer was shown. Pass the "
            "same value in the order-create body. Requires an "
            "authenticated request."
        ),
    )

    def validate(self, attrs):
        """Pickup-point requires a carrier code; home-delivery doesn't.

        Mirrors the order-create body shape: ``shippingProviderCode``
        is bound to ``shippingKind`` semantically and the backend
        cannot route a locker pickup without knowing which carrier's
        locker network to use.
        """
        kind = attrs.get("shipping_kind")
        code = (attrs.get("shipping_provider_code") or "").strip()
        if kind == "pickup_point" and not code:
            raise serializers.ValidationError(
                {
                    "shipping_provider_code": _(
                        "shipping_provider_code is required for pickup_point."
                    )
                }
            )
        # Normalise empty string → not present so the view's
        # downstream None-check stays clean.
        attrs["shipping_provider_code"] = code or None
        return attrs


class CouponApplyRequestSerializer(serializers.Serializer):
    """Request body for ``POST /api/v1/cart/coupon``."""

    code = serializers.CharField(
        max_length=40,
        help_text=_("Coupon code to apply (case-insensitive)"),
    )


class CouponErrorResponseSerializer(serializers.Serializer):
    """4xx body for coupon apply — carries the machine-readable reason."""

    detail = serializers.CharField(
        help_text=_("Human-readable message"),
    )
    reason = serializers.CharField(
        help_text=_(
            "Machine-readable rejection reason (ACP discount-extension "
            "vocabulary, e.g. discount_code_invalid)"
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
