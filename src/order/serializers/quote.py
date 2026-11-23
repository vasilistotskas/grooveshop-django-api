"""The checkout quote on the wire.

``CheckoutQuoteSerializer`` renders an ``order.checkout.quote.CheckoutQuote``:
the body of ``POST /cart/quote`` (with the priced option lists,
``CartQuoteSerializer``) and the ``error.quote`` of an order refused
because its total or its cart changed. ``ExpectedTotalSerializer`` is the
total a client sends back with the order, and ``PickupPointSerializer``
the pickup point both the quote and the order take.
"""

from __future__ import annotations

from django.db.models import prefetch_related_objects
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from product.managers.product import main_image_prefetch
from promotion.enum import CouponRejectionReason
from shipping.enum import FreeShippingSource, ShippingKind


class PickupPointSerializer(serializers.Serializer):
    """The pickup point a checkout chose, by the carrier's own fields
    (its ``location_keys``). Order creation and ``POST /cart/quote``
    take the same fields, so the quote's ``cartDigest`` covers the
    point the order is sent to."""

    # BoxNow locker (required when carrier=boxnow + kind=pickup_point)
    boxnow_locker_id = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        help_text=_("BoxNow APM locker ID from the widget"),
    )
    # ACS station, only honoured when shipping_provider_code='acs'.
    acs_station_external_id = serializers.CharField(
        max_length=32,
        required=False,
        allow_blank=True,
        help_text=_(
            "ACS Smartpoint / shop external ID (Phase 2 pickup-point flow)."
        ),
    )
    acs_station_branch = serializers.CharField(
        max_length=32,
        required=False,
        allow_blank=True,
        help_text=_("ACS_Station_Branch_Destination value."),
    )


class ExpectedTotalSerializer(serializers.Serializer):
    """An order total in whole minor units, compared exactly."""

    amount = serializers.IntegerField(
        min_value=0,
        help_text=_("The amount due in minor units (cents): 1999 for 19.99."),
    )
    currency = serializers.CharField(
        min_length=3,
        max_length=3,
        help_text=_("ISO 4217 currency code of the amount."),
    )

    def validate_currency(self, value: str) -> str:
        return value.upper()


class QuoteAppliedPromotionSerializer(serializers.Serializer):
    promotion_id = serializers.IntegerField(source="promotion.id")
    name = serializers.SerializerMethodField()
    code = serializers.CharField(
        source="code.code",
        allow_null=True,
        help_text=_(
            "The coupon that earned the entry; null for an automatic promotion."
        ),
    )
    amount = serializers.DecimalField(
        source="amount.amount", max_digits=11, decimal_places=2
    )

    def get_name(self, entry) -> str:
        return (
            entry.promotion.safe_translation_getter("name", any_language=True)
            or ""
        )


class QuoteLineSerializer(serializers.Serializer):
    """One cart line as the quote priced it (``QuoteLine``)."""

    id = serializers.IntegerField(
        source="item_id", help_text=_("The cart item the line is.")
    )
    product_id = serializers.IntegerField(source="product.pk")
    product_name = serializers.SerializerMethodField(
        help_text=_("The product's name in the request's language.")
    )
    main_image_path = serializers.CharField(
        source="product.main_image_path",
        help_text=_(
            "Storage path of the product's main image; empty without one."
        ),
    )
    quantity = serializers.IntegerField()
    unit_price = serializers.DecimalField(
        source="unit_price.amount",
        help_text=_("The price per unit the shopper pays, before promotions."),
        max_digits=11,
        decimal_places=2,
    )
    total_price = serializers.DecimalField(
        source="total_price.amount",
        help_text=_("quantity units at unitPrice, before promotions."),
        max_digits=11,
        decimal_places=2,
    )
    promotion_discount = serializers.DecimalField(
        help_text=_(
            "The part of discountTotal this line carries; the lines' "
            "amounts sum to it."
        ),
        max_digits=11,
        decimal_places=2,
    )

    def get_product_name(self, line) -> str:
        return (
            line.product.safe_translation_getter("name", any_language=True)
            or ""
        )


class QuoteAppliedCouponSerializer(serializers.Serializer):
    """What one attached coupon code does for the quoted cart
    (``CouponVerdict``): exactly one of ``amount``, ``freeShipping``,
    ``gift`` and ``rejection`` holds."""

    code = serializers.CharField()
    amount = serializers.DecimalField(
        help_text=_(
            "Money this code takes off; 0 when its benefit is not money, "
            "or it is refused."
        ),
        max_digits=11,
        decimal_places=2,
    )
    free_shipping = serializers.BooleanField(
        help_text=_("This code waives shipping.")
    )
    gift = serializers.BooleanField(
        help_text=_("This code earns a gift the order ships with.")
    )
    rejection = serializers.ChoiceField(
        choices=CouponRejectionReason.choices,
        allow_null=True,
        help_text=_(
            "Why the promotions refuse the attached code now; null while "
            "it applies. Only blockingCoupon stops an order."
        ),
    )


class QuoteBlockingCouponSerializer(serializers.Serializer):
    code = serializers.CharField()
    reason = serializers.CharField(
        help_text=_(
            "Why the code is refused, in the coupon rejection vocabulary "
            "(``discount_code_expired``, ...)."
        ),
    )


def _waived_by_field() -> serializers.ChoiceField:
    return serializers.ChoiceField(
        choices=[(source.value, source.value) for source in FreeShippingSource],
        allow_null=True,
        help_text=_(
            "What made the delivery free: ``threshold`` (the carrier's "
            "free-shipping threshold) or ``promotion`` (a free-shipping "
            "promotion). Null when it is charged, or simply costs nothing."
        ),
    )


class QuoteShippingSerializer(serializers.Serializer):
    price = serializers.DecimalField(
        source="price.amount", max_digits=11, decimal_places=2
    )
    waived_by = _waived_by_field()


class QuotePaymentFeeSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        source="amount.amount", max_digits=11, decimal_places=2
    )
    waived = serializers.BooleanField(
        help_text=_(
            "True when the pay way has a fee and the cart reached its "
            "free-above threshold."
        ),
    )


class QuoteShippingOptionSerializer(serializers.Serializer):
    """One delivery the store offers, at the price this cart would be
    charged for choosing it."""

    provider_code = serializers.CharField()
    provider_name = serializers.CharField()
    kind = serializers.ChoiceField(choices=ShippingKind.choices)
    price = serializers.DecimalField(max_digits=11, decimal_places=2)
    waived_by = _waived_by_field()
    exceeds_max_weight = serializers.BooleanField(
        help_text=_(
            "True when the cart is heavier than this option carries; "
            "choosing it is refused."
        ),
    )
    exceeds_parcel_size = serializers.BooleanField(
        help_text=_(
            "True when the cart does not fit this carrier's parcels; "
            "choosing it is refused."
        ),
    )
    compartment_size = serializers.IntegerField(
        allow_null=True,
        help_text=_(
            "The locker compartment size the cart packs into (1 small, 2 "
            "medium, 3 large) on a BOX NOW pickup option; null elsewhere "
            "and when the cart fits none. Pass it as ``compartmentSize`` "
            "to list the lockers that take this cart."
        ),
    )


class QuotePayWayOptionSerializer(serializers.Serializer):
    """One pay way the shopper is offered, with the fee this cart would
    be charged for choosing it."""

    id = serializers.IntegerField()
    key = serializers.CharField(
        help_text=_("The pay way's key, as on the pay-way endpoint.")
    )
    fee = serializers.DecimalField(max_digits=11, decimal_places=2)
    waived = serializers.BooleanField(
        help_text=_(
            "True when the pay way has a fee and the cart reached its "
            "free-above threshold."
        ),
    )


class CheckoutQuoteSerializer(serializers.Serializer):
    """What checkout charges for a cart, row by row.

    A row that depends on a choice the request did not make is null,
    never guessed: ``shipping`` without a delivery, ``paymentMethodFee``
    without a pay way, and ``giftCardAmount``, ``grandTotal``,
    ``amountDue`` and ``expectedTotal`` without both.
    """

    lines = serializers.SerializerMethodField(
        help_text=_(
            "The cart's lines the quote priced, from the same read as "
            "itemsSubtotal and cartDigest: what the digest covers of each "
            "(product, quantity, unit price, promotion money), with the "
            "product's name and image."
        ),
    )
    items_subtotal = serializers.DecimalField(
        source="items_subtotal.amount",
        help_text=_("The cart's lines at the prices the shopper pays."),
        max_digits=11,
        decimal_places=2,
    )
    discount_total = serializers.DecimalField(
        source="discount_total.amount",
        help_text=_("What live promotions and the applied coupon take off."),
        max_digits=11,
        decimal_places=2,
    )
    applied_promotions = QuoteAppliedPromotionSerializer(
        source="promotion.applied",
        many=True,
        help_text=_(
            "One entry per promotion that took money off; the amounts "
            "sum to discountTotal."
        ),
    )
    applied_coupons = QuoteAppliedCouponSerializer(
        source="coupons",
        many=True,
        help_text=_(
            "The verdict on each coupon code attached to the cart, as "
            "the promotions in this quote decided it."
        ),
    )
    blocking_coupon = QuoteBlockingCouponSerializer(
        allow_null=True,
        help_text=_(
            "An attached coupon order creation refuses this cart for "
            "(``invalid_coupon``). Its discount is not in this quote; "
            "remove the code before ordering. Null when there is none."
        ),
    )
    free_threshold_value = serializers.DecimalField(
        source="free_threshold_value.amount",
        help_text=_(
            "The value every free-above threshold is judged on: the "
            "products after discounts, excluding shipping and fees."
        ),
        max_digits=11,
        decimal_places=2,
    )
    shipping = QuoteShippingSerializer(allow_null=True)
    payment_method_fee = QuotePaymentFeeSerializer(
        source="payment_fee", allow_null=True
    )
    loyalty_discount = serializers.DecimalField(
        source="loyalty_discount.amount",
        help_text=_("What the redeemed loyalty points take off."),
        max_digits=11,
        decimal_places=2,
    )
    gift_card_amount = serializers.DecimalField(
        source="gift_card_amount.amount",
        allow_null=True,
        help_text=_(
            "What the gift cards settle of the total; they are payment, "
            "applied last."
        ),
        max_digits=11,
        decimal_places=2,
    )
    grand_total = serializers.DecimalField(
        source="grand_total.amount",
        allow_null=True,
        help_text=_(
            "The products after promotions, plus shipping and the pay-way fee."
        ),
        max_digits=11,
        decimal_places=2,
    )
    amount_due = serializers.DecimalField(
        source="amount_due.amount",
        allow_null=True,
        help_text=_(
            "What is left to pay after the loyalty discount and the gift "
            "cards: the amount an order created with these choices stores "
            "and is paid for."
        ),
        max_digits=11,
        decimal_places=2,
    )
    currency = serializers.CharField(
        help_text=_("ISO 4217 currency code of every amount in the quote."),
    )
    expected_total = ExpectedTotalSerializer(
        allow_null=True,
        help_text=_(
            "amountDue as order creation compares it. Send it back as "
            "``expectedTotal`` when creating the order: the order is "
            "created only at this total."
        ),
    )
    cart_digest = serializers.CharField(
        allow_null=True,
        help_text=_(
            "An opaque digest of what the order would be made of: the "
            "lines, the promotions and the delivery with its pickup "
            "point, not the pay way. Null until a delivery is chosen. "
            "Send it back as ``expectedCartDigest`` when creating the "
            "order: the order is created only if its cart is still this "
            "one."
        ),
    )

    @extend_schema_field(QuoteLineSerializer(many=True))
    def get_lines(self, quote) -> list:
        # Each line shows its product's main image: one query for all
        # of them, not one per line.
        prefetch_related_objects(
            [line.product for line in quote.lines], main_image_prefetch()
        )
        return list(QuoteLineSerializer(quote.lines, many=True).data)


class CartQuoteSerializer(CheckoutQuoteSerializer):
    """The body of ``POST /cart/quote``: the quote, and what each
    delivery and pay way on offer would cost this cart."""

    shipping_options = serializers.SerializerMethodField(
        help_text=_(
            "Every delivery the store offers to the request's country, "
            "priced as an order would be. Null without a country."
        ),
    )
    pay_ways = serializers.SerializerMethodField(
        help_text=_(
            "Every pay way offered for the chosen delivery (all of them "
            "while none is chosen), with the fee an order would be "
            "charged."
        ),
    )

    @extend_schema_field(
        QuoteShippingOptionSerializer(many=True, allow_null=True)
    )
    def get_shipping_options(self, quote) -> list | None:
        options = self.context["shipping_options"]
        if options is None:
            return None
        return list(QuoteShippingOptionSerializer(options, many=True).data)

    @extend_schema_field(QuotePayWayOptionSerializer(many=True))
    def get_pay_ways(self, quote) -> list:
        return list(
            QuotePayWayOptionSerializer(
                self.context["pay_ways"], many=True
            ).data
        )
