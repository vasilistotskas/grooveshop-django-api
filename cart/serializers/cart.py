from decimal import Decimal

from django.conf import settings
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from cart.models import Cart
from cart.serializers.item import CartItemSerializer
from cart.services import CartService
from promotion.enum import BenefitType, CouponRejectionReason
from promotion.serializers import (
    REWARD_PREVIEW_LIMIT,
    PromotionProductRefSerializer,
)
from shipping.enum import FreeShippingSource


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
    total_vat_value = serializers.SerializerMethodField(
        help_text=_(
            "VAT contained in total_price once promotion_discount is "
            "taken off. A price discount reduces the taxable base, so "
            "this is the figure the invoice will carry. Loyalty points "
            "are redeemed against the order and are not reflected here."
        ),
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
    applied_promotions = serializers.SerializerMethodField(
        help_text=_(
            "Per-promotion breakdown of promotion_discount: one entry "
            "per offer that actually took money off, with the coupon "
            "code that earned it when there was one. The storefront "
            "needs this to tell the shopper WHICH offers applied, and "
            "to show a coupon its OWN amount instead of the cart total."
        ),
    )
    free_threshold_value = serializers.SerializerMethodField(
        help_text=_(
            "The value free shipping and a free payment fee are judged "
            "on: the products after promotion and coupon discounts, "
            "excluding shipping and fees. Compare a free-above threshold "
            "with this, never with total_price."
        ),
    )
    promotion_free_shipping = serializers.SerializerMethodField(
        help_text=_("Whether a live promotion waives the shipping cost"),
    )
    free_shipping_progress = serializers.SerializerMethodField(
        help_text=_(
            "How far this cart is from free shipping, by its cheapest "
            "route: a carrier's free-shipping threshold or a "
            "free-shipping promotion, judged by the rules checkout "
            "prices shipping with. The one figure to show for free "
            "shipping; promotionNearMiss entries flagged freeShipping "
            "are routes it has already weighed. Null when the cart has "
            "no route to free shipping."
        ),
    )
    applied_coupon_codes = serializers.SerializerMethodField(
        help_text=_("Coupon codes currently attached to this cart"),
    )
    applied_coupons = serializers.SerializerMethodField(
        help_text=_(
            "What each attached coupon does for this cart right now: the "
            "money it takes off, the free shipping or gift it earns, or "
            "why the engine refuses it. A code that would change nothing "
            "is refused, so exactly one of the four holds per entry."
        ),
    )
    promotion_gift_items = serializers.SerializerMethodField(
        help_text=_("Free-gift entitlements earned by this cart"),
    )
    promotion_near_miss = serializers.SerializerMethodField(
        help_text=_(
            "Automatic promotions short of exactly one minimum (subtotal, "
            "units, or a reward product) — 'add X more to unlock'"
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

    @staticmethod
    def _codes_apply() -> bool:
        # With promotions off no code applies and the cart lists none, so
        # nothing dead is shown that the shopper could not remove; the
        # codes wait on the cart until the store turns promotions back on.
        from promotion.services import PromotionEngine

        return PromotionEngine.is_enabled()

    @extend_schema_field(OpenApiTypes.DECIMAL)
    def get_total_vat_value(self, obj: Cart):
        line_discounts = self._promotion_result(obj).line_discounts
        return obj.vat_after_discount(line_discounts).amount

    @extend_schema_field(OpenApiTypes.DECIMAL)
    def get_promotion_discount(self, obj: Cart):
        return self._promotion_result(obj).discount_total.amount

    @extend_schema_field(OpenApiTypes.DECIMAL)
    def get_free_threshold_value(self, obj: Cart):
        from order.services import OrderService

        return OrderService.threshold_value(
            obj.total_price, self._promotion_result(obj).discount_total
        ).amount

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_promotion_free_shipping(self, obj: Cart) -> bool:
        return self._promotion_result(obj).free_shipping

    @extend_schema_field(
        {
            "type": "object",
            "nullable": True,
            "properties": {
                "free": {
                    "type": "boolean",
                    "description": (
                        "Checkout charges nothing for shipping: a "
                        "promotion waives it, or the cart reached a "
                        "carrier's threshold"
                    ),
                },
                "source": {
                    "type": "string",
                    "enum": [source.value for source in FreeShippingSource],
                    "description": (
                        "threshold: a carrier's free-shipping threshold, "
                        "for that carrier only; promotion: a "
                        "free-shipping promotion, for every carrier"
                    ),
                },
                "remainingAmount": {
                    "type": "number",
                    "nullable": True,
                    "description": (
                        "Products still missing; null once free, or "
                        "when what is missing is units"
                    ),
                },
                "remainingQuantity": {
                    "type": "integer",
                    "nullable": True,
                    "description": (
                        "Units of the promotion's products still "
                        "missing; null once free, or when what is "
                        "missing is an amount"
                    ),
                },
                "threshold": {
                    "type": "number",
                    "nullable": True,
                    "description": (
                        "The value the route is reached at, for a "
                        "meter; null for a units route or a promotion "
                        "that waives shipping outright"
                    ),
                },
                "countryCode": {
                    "type": "string",
                    "nullable": True,
                    "description": (
                        "The country the carriers' thresholds were "
                        "judged for: the store's first shipping country, "
                        "the one checkout preselects"
                    ),
                },
            },
            "required": [
                "free",
                "source",
                "remainingAmount",
                "remainingQuantity",
                "threshold",
                "countryCode",
            ],
        }
    )
    def get_free_shipping_progress(self, obj: Cart) -> dict | None:
        from order.services import OrderService

        progress = OrderService.free_shipping_progress(
            obj, self._promotion_result(obj)
        )
        if progress is None:
            return None
        return {
            "free": progress.free,
            "source": progress.source.value,
            "remainingAmount": progress.remaining_amount,
            "remainingQuantity": progress.remaining_quantity,
            "threshold": progress.threshold,
            "countryCode": progress.country_code,
        }

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_applied_coupon_codes(self, obj: Cart) -> list[str]:
        # `.all()`, not `.values_list()`: values_list builds a NEW
        # queryset and always hits the database, so it walks straight
        # past the `applied_codes__code` prefetch and costs a query per
        # cart on the staff list. Iterating the prefetched rows uses it.
        if not self._codes_apply():
            return []
        return [row.code.code for row in obj.applied_codes.all()]

    @extend_schema_field(
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "amount": {
                        "type": "number",
                        "description": (
                            "Money this code takes off; 0 when its "
                            "benefit is not money, or it is refused"
                        ),
                    },
                    "freeShipping": {
                        "type": "boolean",
                        "description": "This code waives shipping",
                    },
                    "gift": {
                        "type": "boolean",
                        "description": (
                            "This code earns the gift listed in "
                            "promotionGiftItems"
                        ),
                    },
                    "rejection": {
                        "type": "string",
                        "nullable": True,
                        "enum": [*CouponRejectionReason.values, None],
                        "description": (
                            "Why the engine refuses the attached code "
                            "now; null while it applies"
                        ),
                    },
                },
                "required": [
                    "code",
                    "amount",
                    "freeShipping",
                    "gift",
                    "rejection",
                ],
            },
        }
    )
    def get_applied_coupons(self, obj: Cart) -> list[dict]:
        # A coupon row that only knows its own money cannot tell "free
        # shipping" from "a better offer applies" (demo store: SHIPFREE
        # read "a better offer applies" while it was waiving shipping),
        # nor say why a code that stopped applying is idle. The engine
        # already decided all of it; this hands its verdict over.
        if not self._codes_apply():
            return []
        result = self._promotion_result(obj)
        rejections: dict[str, str] = {}
        for code, reason in result.rejected:
            rejections.setdefault(code, reason)
        benefits = {
            code.code: promotion.benefit_type
            for promotion, code in result.non_monetary
            if code is not None
        }
        entries = []
        for row in obj.applied_codes.all():
            code = row.code.code
            entries.append(
                {
                    "code": code,
                    "amount": sum(
                        (
                            entry.amount.amount
                            for entry in result.applied
                            if entry.code is not None
                            and entry.code.code == code
                        ),
                        Decimal(0),
                    ),
                    "freeShipping": benefits.get(code)
                    == BenefitType.FREE_SHIPPING,
                    "gift": benefits.get(code) == BenefitType.FREE_GIFT,
                    "rejection": rejections.get(code),
                }
            )
        return entries

    @extend_schema_field(
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "promotionId": {"type": "integer"},
                    "name": {"type": "string"},
                    "code": {"type": "string", "nullable": True},
                    "amount": {"type": "number"},
                },
            },
            "description": (
                "One entry per promotion that took money off this "
                "cart. ``amount`` values sum to promotion_discount; "
                "``code`` is the coupon that earned the entry, or null "
                "for an automatic promotion."
            ),
        }
    )
    def get_applied_promotions(self, obj: Cart) -> list[dict]:
        # The engine already knows this — ``CartDiscountResult.applied``
        # is what ``PromotionEngine.record`` writes onto the order as
        # ``metadata["promotions"]``. Serializing it here is what lets
        # the cart explain a discount instead of showing one opaque
        # number, and what lets the coupon row show ITS OWN amount: a
        # coupon that lost the stacking comparison contributes no entry
        # at all, so it can no longer display an automatic promotion's
        # money as though it had earned it.
        return [
            {
                "promotionId": entry.promotion.id,
                "name": entry.promotion.safe_translation_getter(
                    "name", any_language=True
                )
                or "",
                "code": entry.code.code if entry.code else None,
                "amount": entry.amount.amount,
            }
            for entry in self._promotion_result(obj).applied
        ]

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
                    "remainingAmount": {
                        "type": "number",
                        "nullable": True,
                        "description": (
                            "Subtotal still missing; null when the "
                            "missing part is units"
                        ),
                    },
                    "remainingQuantity": {
                        "type": "integer",
                        "nullable": True,
                        "description": (
                            "Eligible units still missing (a minimum "
                            "quantity, or the rest of a buy-X-get-Y "
                            "group, or of a reward product); null when "
                            "the missing part is subtotal"
                        ),
                    },
                    "rewardProducts": {
                        "type": "array",
                        "items": {
                            "$ref": "#/components/schemas/PromotionProductRef"
                        },
                        "description": (
                            "The products to add when what is missing is "
                            "an offer's reward (a case bought, its screen "
                            "protector at -50% not yet); empty otherwise"
                        ),
                    },
                    "freeShipping": {
                        "type": "boolean",
                        "description": (
                            "The offer waives shipping, so it is one of "
                            "the routes freeShippingProgress already "
                            "weighed; show that, not this teaser"
                        ),
                    },
                },
                "required": [
                    "promotionId",
                    "name",
                    "remainingAmount",
                    "remainingQuantity",
                    "rewardProducts",
                    "freeShipping",
                ],
            },
            "description": (
                "Automatic promotions the cart ALMOST qualifies for: "
                "exactly one of remainingAmount and remainingQuantity is "
                "set. Powers the 'add X more to unlock' teaser; each one "
                "is checked against the engine, so following it really "
                "does unlock the offer."
            ),
        }
    )
    def get_promotion_near_miss(self, obj: Cart) -> list[dict]:
        near_miss = self._promotion_result(obj).near_miss
        rewards = self._reward_products(near_miss)
        return [
            {
                "promotionId": entry.promotion.id,
                "name": entry.promotion.safe_translation_getter(
                    "name", any_language=True
                )
                or "",
                "remainingAmount": entry.remaining_amount,
                "remainingQuantity": entry.remaining_quantity,
                "rewardProducts": PromotionProductRefSerializer(
                    [
                        rewards[product.pk]
                        for product in entry.reward_products
                        if product.pk in rewards
                    ][:REWARD_PREVIEW_LIMIT],
                    many=True,
                    context=self.context,
                ).data,
                "freeShipping": entry.promotion.benefit_type
                == BenefitType.FREE_SHIPPING,
            }
            for entry in near_miss
        ]

    @staticmethod
    def _reward_products(near_miss) -> dict:
        """The teasers' reward products, card-ready, in one query.

        The engine's rows come from the promotion's ``get_products``
        prefetch, which carries neither translations nor the main
        image; reading those per product would cost two queries each.
        """
        ids = {
            product.pk
            for entry in near_miss
            for product in entry.reward_products
        }
        if not ids:
            return {}
        from product.models.product import Product

        return {
            product.pk: product
            for product in Product.objects.filter(pk__in=ids)
            .with_translations()
            .with_main_image()
        }

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
            "free_threshold_value",
            "applied_promotions",
            "promotion_free_shipping",
            "free_shipping_progress",
            "applied_coupon_codes",
            "applied_coupons",
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
            "free_threshold_value",
            "applied_promotions",
            "promotion_free_shipping",
            "free_shipping_progress",
            "applied_coupon_codes",
            "applied_coupons",
            "promotion_gift_items",
            "promotion_near_miss",
            "b2b_pricing",
            "created_at",
            "updated_at",
            "last_activity",
        )


class CartItemMutationResponseSerializer(CartItemSerializer):
    """The line an add or a quantity change wrote, plus the whole cart it
    now sits in, so a client shows the new state without a second ``GET``.

    ``cart`` is the same body as ``GET /cart``, read from the cart the
    request resolved (``context["cart"]``) after the write.
    """

    cart = serializers.SerializerMethodField(
        help_text=_("The updated cart, as returned by the cart detail endpoint")
    )

    @extend_schema_field(CartSerializer)
    def get_cart(self, obj) -> dict:
        cart = CartService.load_for_detail(self.context["cart"])
        return CartSerializer(cart, context=self.context).data

    class Meta(CartItemSerializer.Meta):
        fields = (*CartItemSerializer.Meta.fields, "cart")


# A reservation exists per cart line, and this endpoint does database
# work plus one release attempt per id. Unbounded, that is a cheap way
# for any caller to buy an arbitrarily long request. A hundred is far
# above any real cart — `gift_card_codes` in this same file has been
# capped at 3 all along, so the convention was already here.
MAX_RELEASE_RESERVATION_IDS = 100


class ReleaseReservationsRequestSerializer(serializers.Serializer):
    """Serializer for releasing stock reservations."""

    reservation_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=True,
        max_length=MAX_RELEASE_RESERVATION_IDS,
        # `format_lazy`, not an f-string inside `_()`: the f-string is
        # resolved before `_()` sees it, so the msgid would carry the
        # actual number and never match the catalogue (INT001, and the
        # same trap `UserAccount.username`'s help_text documents).
        help_text=format_lazy(
            _("List of reservation IDs to release (at most {max})"),
            max=MAX_RELEASE_RESERVATION_IDS,
        ),
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


class CartAgentTokenResponseSerializer(serializers.Serializer):
    """Response of ``POST /api/v1/cart/agent-token``."""

    token = serializers.CharField(
        help_text=_("Send as the X-Cart-Token header.")
    )
    expires_in = serializers.IntegerField(
        help_text=_("Seconds until the token stops working.")
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
            "pickup_point). Required so the per-kind gates (ACS "
            "lockers need synced stations) and BoxNow's PICKUP_POINT "
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
        help_text=_(
            "ISO 3166-1 alpha-2 country code — required because a "
            "``ShippingRate`` is per-country, so there is no priceable "
            "shipping option without a destination. Match what the "
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
                },
                code="shipping_provider_required",
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
    code = serializers.CharField(
        required=False, help_text=_("The same value as ``reason``.")
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
