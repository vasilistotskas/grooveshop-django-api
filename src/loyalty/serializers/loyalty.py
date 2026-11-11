from rest_framework import serializers

from loyalty.models.transaction import PointsTransaction
from loyalty.serializers.tier import LoyaltyTierSerializer


class LoyaltySummarySerializer(serializers.Serializer):
    """Serializer for the user's loyalty summary response.

    Returns computed loyalty data: balance, XP, level, tier, and progress.
    """

    points_balance = serializers.IntegerField(
        read_only=True,
        help_text="Current spendable points balance.",
    )
    total_xp = serializers.IntegerField(
        read_only=True,
        help_text="Cumulative experience points.",
    )
    level = serializers.IntegerField(
        read_only=True,
        help_text="Current level computed from total_xp.",
    )
    tier = LoyaltyTierSerializer(
        read_only=True,
        allow_null=True,
        help_text="Current loyalty tier with translations, or null if none.",
    )
    points_to_next_tier = serializers.IntegerField(
        read_only=True,
        allow_null=True,
        help_text="XP points needed to reach the next tier, or null if at highest.",
    )


class PointsTransactionSerializer(
    serializers.ModelSerializer[PointsTransaction]
):
    """Serializer for points transaction history records."""

    class Meta:
        model = PointsTransaction
        fields = (
            "id",
            "points",
            "transaction_type",
            "reference_order",
            "description",
            "created_at",
        )
        read_only_fields = (
            "id",
            "points",
            "transaction_type",
            "reference_order",
            "description",
            "created_at",
        )


class RedeemPointsRequestSerializer(serializers.Serializer):
    """Serializer for validating a points redemption request."""

    points_amount = serializers.IntegerField(
        min_value=1,
        help_text="Number of points to redeem. Must be a positive integer.",
    )
    currency = serializers.ChoiceField(
        choices=[("EUR", "EUR"), ("USD", "USD")],
        help_text="Currency for the monetary discount (EUR or USD).",
    )
    order_id = serializers.IntegerField(
        min_value=1,
        help_text="Order ID to associate the redemption with. "
        "Used to cap the discount to the order's products total.",
    )


class RedeemPointsResponseSerializer(serializers.Serializer):
    """Serializer for the points redemption response."""

    discount_amount = serializers.DecimalField(
        max_digits=11,
        decimal_places=2,
        read_only=True,
        help_text="Monetary discount value in the requested currency.",
    )
    currency = serializers.CharField(
        read_only=True,
        help_text="Currency of the discount.",
    )
    points_redeemed = serializers.IntegerField(
        read_only=True,
        help_text="Number of points that were redeemed.",
    )
    remaining_balance = serializers.IntegerField(
        read_only=True,
        help_text="User's points balance after redemption.",
    )


class ProductPointsSerializer(serializers.Serializer):
    """Serializer for product points preview response."""

    product_id = serializers.IntegerField(
        read_only=True,
        help_text="ID of the product.",
    )
    potential_points = serializers.IntegerField(
        read_only=True,
        help_text="Points the user would earn by purchasing this product.",
    )
    tier_multiplier_applied = serializers.BooleanField(
        read_only=True,
        help_text="Whether the user's tier multiplier was applied to the calculation.",
    )
