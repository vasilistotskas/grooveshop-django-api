import logging
import math
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from extra_settings.models import Setting

from loyalty.enum import PriceBasis, TransactionType
from loyalty.models.tier import LoyaltyTier
from loyalty.models.transaction import PointsTransaction

logger = logging.getLogger(__name__)


class LoyaltyService:
    """Core business logic for the loyalty points and ranking system.

    All methods are classmethods that read configuration from django-extra-settings
    via Setting.get(). The service operates on an append-only transaction ledger —
    a user's balance is always derived by summing their PointsTransaction records.
    """

    @classmethod
    def is_enabled(cls) -> bool:
        """Check if the loyalty system is enabled via Setting.get('LOYALTY_ENABLED')."""
        return bool(Setting.get("LOYALTY_ENABLED", default=False))

    @classmethod
    def get_price_basis_amount(cls, product) -> Decimal:
        """Return the price amount based on the configured LOYALTY_PRICE_BASIS.

        Supports four price basis options:
        - price_excl_vat_no_discount: product.price
        - price_excl_vat_with_discount: product.price - product.discount_value
        - price_incl_vat_no_discount: product.price + product.vat_value
        - final_price: product.final_price (price + vat - discount)
        """
        basis = Setting.get("LOYALTY_PRICE_BASIS", default="final_price")
        if basis == PriceBasis.PRICE_EXCL_VAT_NO_DISCOUNT:
            return Decimal(str(product.price.amount))
        elif basis == PriceBasis.PRICE_EXCL_VAT_WITH_DISCOUNT:
            return Decimal(str(product.price.amount)) - Decimal(
                str(product.discount_value.amount)
            )
        elif basis == PriceBasis.PRICE_INCL_VAT_NO_DISCOUNT:
            return Decimal(str(product.price.amount)) + Decimal(
                str(product.vat_value.amount)
            )
        else:  # FINAL_PRICE (default)
            return Decimal(str(product.final_price.amount))

    @classmethod
    def calculate_item_points(
        cls,
        product,
        quantity: int,
        tier_multiplier: Decimal = Decimal("1.0"),
    ) -> int:
        """Calculate points for a single order item.

        Formula: floor(price_basis * factor * coefficient * quantity [* tier_multiplier])
                 + (product.points * quantity)

        The tier multiplier is only applied when LOYALTY_TIER_MULTIPLIER_ENABLED is true
        and the multiplier is greater than 1.0.
        """
        factor = Decimal(str(Setting.get("LOYALTY_POINTS_FACTOR", default=1.0)))
        price_basis = cls.get_price_basis_amount(product)
        calculated = (
            price_basis
            * factor
            * Decimal(str(product.points_coefficient))
            * quantity
        )

        tier_enabled = Setting.get(
            "LOYALTY_TIER_MULTIPLIER_ENABLED", default=False
        )
        if tier_enabled and tier_multiplier > Decimal("1.0"):
            calculated = calculated * tier_multiplier

        return math.floor(calculated) + (product.points * quantity)

    @classmethod
    @transaction.atomic
    def award_order_points(cls, order_id: int) -> int:
        """Award points for all items in a completed order.

        Returns total points awarded. Idempotent — checks for existing EARN
        transactions before creating new ones.
        """
        if not cls.is_enabled():
            return 0

        from order.models.order import Order

        try:
            order = Order.objects.select_related("user").get(id=order_id)
        except Order.DoesNotExist:
            logger.error("Order %s not found for loyalty points", order_id)
            return 0

        if not order.user_id:
            return 0

        # Idempotency check — skip if EARN transactions already exist for this order
        if PointsTransaction.objects.get_earn_transactions_for_order(
            order
        ).exists():
            logger.info(
                "Points already awarded for order %s, skipping", order_id
            )
            return 0

        user = order.user
        tier_multiplier = Decimal("1.0")
        if user.loyalty_tier:
            tier_multiplier = user.loyalty_tier.points_multiplier

        total_points = 0
        for item in order.items.select_related("product", "product__vat").all():
            points = cls.calculate_item_points(
                item.product, item.quantity, tier_multiplier
            )
            PointsTransaction.objects.create(
                user=user,
                points=points,
                transaction_type=TransactionType.EARN,
                reference_order=order,
                description=f"Points earned for {item.product} x{item.quantity}",
            )
            total_points += points

        # Update XP
        user.total_xp += total_points
        user.save(update_fields=["total_xp"])

        return total_points

    @classmethod
    @transaction.atomic
    def reverse_order_points(cls, order_id: int) -> int:
        """Reverse all EARN transactions for an order.

        Returns total points reversed. Creates ADJUST transactions that negate
        the original EARN amounts, clamping to prevent negative balance.
        """
        if not cls.is_enabled():
            return 0

        from order.models.order import Order

        try:
            order = Order.objects.select_related("user").get(id=order_id)
        except Order.DoesNotExist:
            logger.error("Order %s not found for loyalty reversal", order_id)
            return 0

        if not order.user_id:
            return 0

        user = order.user
        earn_transactions = (
            PointsTransaction.objects.get_earn_transactions_for_order(order)
        )

        if not earn_transactions.exists():
            return 0

        # Check if already reversed — skip if ADJUST transactions exist for this order
        existing_adjustments = PointsTransaction.objects.filter(
            reference_order=order,
            transaction_type=TransactionType.ADJUST,
        )
        if existing_adjustments.exists():
            logger.info(
                "Points already reversed for order %s, skipping", order_id
            )
            return 0

        total_reversed = 0
        current_balance = cls.get_user_balance(user)

        for earn_tx in earn_transactions:
            reversal_amount = earn_tx.points
            # Clamp if balance would go negative
            if current_balance - reversal_amount < 0:
                reversal_amount = current_balance
                logger.warning(
                    "Clamping reversal for user %s: expected %s, actual %s",
                    user.id,
                    earn_tx.points,
                    reversal_amount,
                )

            if reversal_amount > 0:
                PointsTransaction.objects.create(
                    user=user,
                    points=-reversal_amount,
                    transaction_type=TransactionType.ADJUST,
                    reference_order=order,
                    description=f"Points reversed for order #{order.id}",
                )
                current_balance -= reversal_amount
                total_reversed += reversal_amount

        # Subtract XP, clamped to 0
        user.total_xp = max(0, user.total_xp - total_reversed)
        user.save(update_fields=["total_xp"])

        return total_reversed

    @classmethod
    @transaction.atomic
    def redeem_points(
        cls, user, points_amount: int, currency: str, order=None
    ) -> Decimal:
        """Redeem points for a monetary discount.

        Returns the discount amount. Validates that the loyalty system is enabled,
        the points amount is positive, the currency is supported, and the user
        has sufficient balance.

        If an order is provided, stores loyalty_points_redeemed and loyalty_discount
        in the Order's metadata JSON field and sets reference_order on the transaction.
        """
        if not cls.is_enabled():
            raise ValidationError(_("Loyalty system is currently disabled."))

        if points_amount <= 0:
            raise ValidationError(_("Points amount must be positive."))

        supported_currencies = {"EUR", "USD"}
        if currency not in supported_currencies:
            raise ValidationError(
                _("Unsupported currency: %(currency)s. Supported: EUR, USD")
                % {"currency": currency}
            )

        balance = cls.get_user_balance(user)
        if points_amount > balance:
            raise ValidationError(
                _(
                    "Insufficient points balance. Available: %(balance)s, Requested: %(amount)s"
                )
                % {"balance": balance, "amount": points_amount}
            )

        ratio_key = f"LOYALTY_REDEMPTION_RATIO_{currency}"
        ratio = Decimal(str(Setting.get(ratio_key, default=100.0)))
        discount = Decimal(str(points_amount)) / ratio

        PointsTransaction.objects.create(
            user=user,
            points=-points_amount,
            transaction_type=TransactionType.REDEEM,
            reference_order=order,
            description=f"Redeemed {points_amount} points for {discount} {currency} discount",
        )

        # Store redemption metadata in Order if provided (Requirement 4.5)
        if order is not None:
            order.store_value_in_metadata(
                {
                    "loyalty_points_redeemed": points_amount,
                    "loyalty_discount": str(discount),
                }
            )

        return discount

    @classmethod
    def get_user_balance(cls, user) -> int:
        """Get user's current points balance from the transaction ledger."""
        return PointsTransaction.objects.get_balance(user)

    @classmethod
    def get_user_level(cls, user) -> int:
        """Calculate user's level from total_xp.

        Formula: 1 + floor(total_xp / XP_PER_LEVEL)
        """
        xp_per_level = Setting.get("LOYALTY_XP_PER_LEVEL", default=1000)
        if xp_per_level <= 0:
            return 1
        return 1 + (user.total_xp // xp_per_level)

    @classmethod
    def get_user_tier(cls, user) -> LoyaltyTier | None:
        """Get the highest qualifying tier for user's current level."""
        level = cls.get_user_level(user)
        return LoyaltyTier.objects.get_for_level(level)

    @classmethod
    def recalculate_tier(cls, user) -> None:
        """Recalculate and update user's tier based on current XP."""
        new_tier = cls.get_user_tier(user)
        new_tier_id = new_tier.id if new_tier else None
        if user.loyalty_tier_id != new_tier_id:
            user.loyalty_tier = new_tier
            user.save(update_fields=["loyalty_tier"])

    @classmethod
    def process_expiration(cls) -> int:
        """Process all expired points.

        Returns count of EXPIRE transactions created. When
        LOYALTY_POINTS_EXPIRATION_DAYS is 0, no expiration occurs.
        """
        expiration_days = Setting.get(
            "LOYALTY_POINTS_EXPIRATION_DAYS", default=0
        )
        if expiration_days <= 0:
            return 0

        cutoff_date = timezone.now() - timedelta(days=expiration_days)
        expirable = PointsTransaction.objects.get_expirable_transactions(
            cutoff_date
        )

        count = 0
        for earn_tx in expirable:
            PointsTransaction.objects.create(
                user=earn_tx.user,
                points=-earn_tx.points,
                transaction_type=TransactionType.EXPIRE,
                description=f"Points expired from transaction {earn_tx.id}",
            )
            count += 1

        return count

    @classmethod
    def get_product_potential_points(cls, product, user=None) -> int:
        """Calculate potential points for a single product purchase.

        If a user is provided and has a tier, the tier multiplier is applied.
        """
        if not cls.is_enabled():
            return 0

        tier_multiplier = Decimal("1.0")
        if user and user.loyalty_tier:
            tier_multiplier = user.loyalty_tier.points_multiplier

        return cls.calculate_item_points(
            product, quantity=1, tier_multiplier=tier_multiplier
        )

    @classmethod
    def check_new_customer_bonus(cls, user, order) -> int:
        """Award new customer bonus if applicable.

        Returns bonus points awarded, or 0 if not applicable. The bonus is
        awarded only on the user's first order (no prior EARN transactions
        excluding the current order).
        """
        if not Setting.get("LOYALTY_NEW_CUSTOMER_BONUS_ENABLED", default=False):
            return 0

        # Check if user has any prior EARN transactions (excluding current order)
        if (
            PointsTransaction.objects.filter(
                user=user,
                transaction_type=TransactionType.EARN,
            )
            .exclude(reference_order=order)
            .exists()
        ):
            return 0

        bonus_points = Setting.get(
            "LOYALTY_NEW_CUSTOMER_BONUS_POINTS", default=100
        )
        PointsTransaction.objects.create(
            user=user,
            points=bonus_points,
            transaction_type=TransactionType.BONUS,
            reference_order=order,
            description="New customer bonus",
        )

        return bonus_points
