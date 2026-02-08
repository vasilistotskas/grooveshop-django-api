"""Integration tests for the full order lifecycle with loyalty points.

Tests the complete flow through services (award, reverse, redeem, bonus)
with real database operations, verifying EARN/ADJUST/REDEEM/BONUS transactions,
XP accumulation, tier assignment, and order metadata.

Requirements: 3.1-3.5, 4.1-4.5, 6.1-6.4, 9.1-9.3
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.conf import settings
from djmoney.money import Money

from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction
from loyalty.models.tier import LoyaltyTier
from loyalty.services import LoyaltyService
from order.models.item import OrderItem
from order.models.order import Order
from product.models.product import Product
from user.factories.account import UserAccountFactory
from vat.models import Vat


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _loyalty_settings(
    enabled=True,
    points_factor=1.0,
    price_basis="final_price",
    tier_multiplier_enabled=False,
    xp_per_level=1000,
    new_customer_bonus_enabled=False,
    new_customer_bonus_points=100,
    redemption_ratio_eur=100.0,
    redemption_ratio_usd=100.0,
    expiration_days=0,
):
    """Return a mock side_effect for Setting.get() with loyalty settings."""
    settings_map = {
        "LOYALTY_ENABLED": enabled,
        "LOYALTY_POINTS_FACTOR": points_factor,
        "LOYALTY_PRICE_BASIS": price_basis,
        "LOYALTY_TIER_MULTIPLIER_ENABLED": tier_multiplier_enabled,
        "LOYALTY_XP_PER_LEVEL": xp_per_level,
        "LOYALTY_NEW_CUSTOMER_BONUS_ENABLED": new_customer_bonus_enabled,
        "LOYALTY_NEW_CUSTOMER_BONUS_POINTS": new_customer_bonus_points,
        "LOYALTY_REDEMPTION_RATIO_EUR": redemption_ratio_eur,
        "LOYALTY_REDEMPTION_RATIO_USD": redemption_ratio_usd,
        "LOYALTY_POINTS_EXPIRATION_DAYS": expiration_days,
    }

    def _get(key, default=None):
        return settings_map.get(key, default)

    return _get


def _create_product(
    price=Decimal("50.00"),
    vat_percent=Decimal("24.0"),
    discount_percent=Decimal("0.0"),
    points_coefficient=Decimal("1.00"),
    bonus_points=0,
):
    """Create a Product with optional Vat in the database."""
    vat = None
    if vat_percent > 0:
        vat = Vat.objects.create(value=vat_percent)

    return Product.objects.create(
        price=Money(price, settings.DEFAULT_CURRENCY),
        discount_percent=discount_percent,
        vat=vat,
        points_coefficient=points_coefficient,
        points=bonus_points,
        stock=100,
        active=True,
    )


def _create_order(user):
    """Create an Order for the given user."""
    return Order.objects.create(
        user=user,
        email="test@example.com",
        first_name="Test",
        last_name="User",
        street="Test Street",
        street_number="1",
        city="Test City",
        zipcode="12345",
        phone="+306900000000",
        status="COMPLETED",
    )


def _create_order_item(order, product, quantity=1):
    """Create an OrderItem linking an order to a product."""
    return OrderItem.objects.create(
        order=order,
        product=product,
        price=product.price,
        quantity=quantity,
    )


def _create_tier(required_level, points_multiplier=Decimal("1.0"), name="Tier"):
    """Create a LoyaltyTier with a translation."""
    tier = LoyaltyTier.objects.create(
        required_level=required_level,
        points_multiplier=points_multiplier,
    )
    # Create a translation for the tier
    from django.apps import apps

    LoyaltyTierTranslation = apps.get_model("loyalty", "LoyaltyTierTranslation")
    LoyaltyTierTranslation.objects.create(
        master=tier,
        language_code="en",
        name=name,
        description=f"{name} tier",
    )
    return tier


# ---------------------------------------------------------------------------
# Test 1: Order complete → EARN transactions + XP + tier
# Requirements: 3.1, 3.2, 3.3, 3.5, 8.2, 8.5
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOrderCompleteEarnFlow:
    """Test: create order → complete → verify EARN transactions + XP + tier."""

    def test_order_complete_creates_earn_transactions_and_updates_xp(self):
        """Completing an order awards EARN transactions per item and adds XP."""
        user = UserAccountFactory()
        product1 = _create_product(
            price=Decimal("100.00"), vat_percent=Decimal("0.0")
        )
        product2 = _create_product(
            price=Decimal("50.00"), vat_percent=Decimal("0.0"), bonus_points=10
        )
        order = _create_order(user)
        _create_order_item(order, product1, quantity=2)
        _create_order_item(order, product2, quantity=1)

        mock_settings = _loyalty_settings(
            enabled=True,
            points_factor=1.0,
            price_basis="final_price",
        )

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            total_points = LoyaltyService.award_order_points(order.id)

        # product1: floor(100 * 1.0 * 1.0 * 2) + 0 = 200
        # product2: floor(50 * 1.0 * 1.0 * 1) + 10 = 60
        assert total_points == 260

        # Verify EARN transactions created — one per order item
        earn_txs = PointsTransaction.objects.filter(
            user=user,
            transaction_type=TransactionType.EARN,
            reference_order=order,
        )
        assert earn_txs.count() == 2
        assert sum(tx.points for tx in earn_txs) == 260

        # Verify XP updated on user
        user.refresh_from_db()
        assert user.total_xp == 260

    def test_order_complete_assigns_correct_tier(self):
        """After earning XP, the user's tier is recalculated correctly."""
        user = UserAccountFactory()
        _create_tier(
            required_level=1, points_multiplier=Decimal("1.0"), name="Bronze"
        )
        gold_tier = _create_tier(
            required_level=3, points_multiplier=Decimal("1.5"), name="Gold"
        )

        # Give user enough XP for level 3: level = 1 + floor(2500/1000) = 3
        user.total_xp = 2500
        user.save(update_fields=["total_xp"])

        mock_settings = _loyalty_settings(enabled=True, xp_per_level=1000)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            LoyaltyService.recalculate_tier(user)

        user.refresh_from_db()
        assert user.loyalty_tier == gold_tier

    def test_order_complete_with_tier_multiplier(self):
        """Tier multiplier is applied to calculated points when enabled."""
        user = UserAccountFactory()
        gold_tier = _create_tier(
            required_level=1, points_multiplier=Decimal("2.0"), name="Gold"
        )
        user.loyalty_tier = gold_tier
        user.save(update_fields=["loyalty_tier"])

        product = _create_product(
            price=Decimal("100.00"), vat_percent=Decimal("0.0")
        )
        order = _create_order(user)
        _create_order_item(order, product, quantity=1)

        mock_settings = _loyalty_settings(
            enabled=True,
            points_factor=1.0,
            tier_multiplier_enabled=True,
        )

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            total_points = LoyaltyService.award_order_points(order.id)

        # floor(100 * 1.0 * 1.0 * 1 * 2.0) + 0 = 200
        assert total_points == 200

        user.refresh_from_db()
        assert user.total_xp == 200


# ---------------------------------------------------------------------------
# Test 2: Order complete → cancel → ADJUST transactions + XP reversal
# Requirements: 6.1, 6.2, 6.3, 6.4
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOrderCompleteThenCancelFlow:
    """Test: create order → complete → cancel → verify ADJUST transactions + XP reversal."""

    def test_cancel_reverses_earn_with_adjust_transactions(self):
        """Canceling an order creates ADJUST transactions negating the EARN sum."""
        user = UserAccountFactory()
        product = _create_product(
            price=Decimal("80.00"), vat_percent=Decimal("0.0")
        )
        order = _create_order(user)
        _create_order_item(order, product, quantity=3)

        mock_settings = _loyalty_settings(enabled=True, points_factor=1.0)

        # Step 1: Award points
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            earned = LoyaltyService.award_order_points(order.id)

        # floor(80 * 1.0 * 1.0 * 3) = 240
        assert earned == 240
        user.refresh_from_db()
        assert user.total_xp == 240

        # Step 2: Reverse points (simulating cancel)
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            reversed_pts = LoyaltyService.reverse_order_points(order.id)

        assert reversed_pts == 240

        # Verify ADJUST transactions created
        adjust_txs = PointsTransaction.objects.filter(
            user=user,
            transaction_type=TransactionType.ADJUST,
            reference_order=order,
        )
        assert adjust_txs.count() == 1
        assert sum(tx.points for tx in adjust_txs) == -240

        # Verify balance is zero
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            balance = LoyaltyService.get_user_balance(user)
        assert balance == 0

        # Verify XP subtracted
        user.refresh_from_db()
        assert user.total_xp == 0

    def test_cancel_clamps_xp_to_zero(self):
        """XP is clamped to zero if reversal exceeds current XP."""
        user = UserAccountFactory()
        product = _create_product(
            price=Decimal("100.00"), vat_percent=Decimal("0.0")
        )
        order = _create_order(user)
        _create_order_item(order, product, quantity=1)

        mock_settings = _loyalty_settings(enabled=True, points_factor=1.0)

        # Award points
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            LoyaltyService.award_order_points(order.id)

        # Manually reduce XP to simulate partial spend elsewhere
        user.total_xp = 30
        user.save(update_fields=["total_xp"])

        # Reverse — should clamp XP to 0 (not go negative)
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            LoyaltyService.reverse_order_points(order.id)

        user.refresh_from_db()
        assert user.total_xp == 0

    def test_cancel_recalculates_tier_downward(self):
        """After reversal, tier is recalculated and may downgrade."""
        user = UserAccountFactory()
        bronze = _create_tier(required_level=1, name="Bronze")
        _create_tier(required_level=5, name="Gold")

        # User at level 5: 1 + floor(4000/1000) = 5
        user.total_xp = 4000
        user.save(update_fields=["total_xp"])

        mock_settings = _loyalty_settings(enabled=True, xp_per_level=1000)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            LoyaltyService.recalculate_tier(user)

        user.refresh_from_db()
        assert user.loyalty_tier is not None
        assert user.loyalty_tier.required_level == 5

        # Simulate XP drop to level 2: 1 + floor(1500/1000) = 2
        user.total_xp = 1500
        user.save(update_fields=["total_xp"])

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            LoyaltyService.recalculate_tier(user)

        user.refresh_from_db()
        assert user.loyalty_tier == bronze


# ---------------------------------------------------------------------------
# Test 3: Order complete → redeem points → REDEEM transaction + metadata
# Requirements: 4.1, 4.2, 4.3, 4.4, 4.5
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOrderCompleteAndRedeemFlow:
    """Test: create order → complete → redeem points → verify REDEEM transaction + metadata."""

    def test_redeem_creates_negative_redeem_transaction(self):
        """Redeeming points creates a REDEEM transaction with negative points."""
        user = UserAccountFactory()
        product = _create_product(
            price=Decimal("200.00"), vat_percent=Decimal("0.0")
        )
        order = _create_order(user)
        _create_order_item(order, product, quantity=1)

        mock_settings = _loyalty_settings(
            enabled=True,
            points_factor=1.0,
            redemption_ratio_eur=100.0,
        )

        # Step 1: Award points
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            earned = LoyaltyService.award_order_points(order.id)

        assert earned == 200

        # Step 2: Redeem 100 points
        redeem_order = _create_order(user)
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            discount = LoyaltyService.redeem_points(
                user, points_amount=100, currency="EUR", order=redeem_order
            )

        # 100 / 100.0 = 1.00 EUR discount
        assert discount == Decimal("1")

        # Verify REDEEM transaction
        redeem_txs = PointsTransaction.objects.filter(
            user=user,
            transaction_type=TransactionType.REDEEM,
        )
        assert redeem_txs.count() == 1
        redeem_tx = redeem_txs.first()
        assert redeem_tx.points == -100
        assert redeem_tx.reference_order == redeem_order

        # Verify remaining balance
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            balance = LoyaltyService.get_user_balance(user)
        assert balance == 100  # 200 earned - 100 redeemed

    def test_redeem_stores_metadata_on_order(self):
        """Redemption stores loyalty_points_redeemed and loyalty_discount in order metadata."""
        user = UserAccountFactory()
        product = _create_product(
            price=Decimal("500.00"), vat_percent=Decimal("0.0")
        )
        order = _create_order(user)
        _create_order_item(order, product, quantity=1)

        mock_settings = _loyalty_settings(
            enabled=True,
            points_factor=1.0,
            redemption_ratio_eur=50.0,
        )

        # Award points
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            LoyaltyService.award_order_points(order.id)

        # Redeem 200 points on a new order
        redeem_order = _create_order(user)
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            discount = LoyaltyService.redeem_points(
                user, points_amount=200, currency="EUR", order=redeem_order
            )

        # 200 / 50.0 = 4.00 EUR
        assert discount == Decimal("4")

        # Verify order metadata
        redeem_order.refresh_from_db()
        assert redeem_order.metadata["loyalty_points_redeemed"] == 200
        assert redeem_order.metadata["loyalty_discount"] == "4"

    def test_redeem_rejects_over_balance(self):
        """Attempting to redeem more than balance raises ValidationError."""
        from django.core.exceptions import ValidationError

        user = UserAccountFactory()
        product = _create_product(
            price=Decimal("50.00"), vat_percent=Decimal("0.0")
        )
        order = _create_order(user)
        _create_order_item(order, product, quantity=1)

        mock_settings = _loyalty_settings(enabled=True, points_factor=1.0)

        # Award 50 points
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            LoyaltyService.award_order_points(order.id)

        # Try to redeem 100 — should fail
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            with pytest.raises(
                ValidationError, match="Insufficient points balance"
            ):
                LoyaltyService.redeem_points(
                    user, points_amount=100, currency="EUR"
                )

        # Verify no REDEEM transaction was created
        assert (
            PointsTransaction.objects.filter(
                user=user, transaction_type=TransactionType.REDEEM
            ).count()
            == 0
        )


# ---------------------------------------------------------------------------
# Test 4: New customer bonus on first order
# Requirements: 9.1, 9.2, 9.3
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestNewCustomerBonusFlow:
    """Test: new customer bonus on first order."""

    def test_first_order_awards_bonus(self):
        """First completed order awards a BONUS transaction when enabled."""
        user = UserAccountFactory()
        product = _create_product(
            price=Decimal("100.00"), vat_percent=Decimal("0.0")
        )
        order = _create_order(user)
        _create_order_item(order, product, quantity=1)

        mock_settings = _loyalty_settings(
            enabled=True,
            points_factor=1.0,
            new_customer_bonus_enabled=True,
            new_customer_bonus_points=150,
        )

        # Award order points first
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            earned = LoyaltyService.award_order_points(order.id)
            bonus = LoyaltyService.check_new_customer_bonus(user, order)

        assert earned == 100
        assert bonus == 150

        # Verify BONUS transaction
        bonus_txs = PointsTransaction.objects.filter(
            user=user,
            transaction_type=TransactionType.BONUS,
        )
        assert bonus_txs.count() == 1
        bonus_tx = bonus_txs.first()
        assert bonus_tx.points == 150
        assert bonus_tx.reference_order == order
        assert "New customer bonus" in bonus_tx.description

        # Verify total balance = earned + bonus
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            balance = LoyaltyService.get_user_balance(user)
        assert balance == 250  # 100 + 150

    def test_second_order_does_not_award_bonus(self):
        """Second order does not award a bonus even when enabled."""
        user = UserAccountFactory()
        product = _create_product(
            price=Decimal("100.00"), vat_percent=Decimal("0.0")
        )

        # First order
        order1 = _create_order(user)
        _create_order_item(order1, product, quantity=1)

        mock_settings = _loyalty_settings(
            enabled=True,
            points_factor=1.0,
            new_customer_bonus_enabled=True,
            new_customer_bonus_points=150,
        )

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            LoyaltyService.award_order_points(order1.id)
            LoyaltyService.check_new_customer_bonus(user, order1)

        # Second order
        product2 = _create_product(
            price=Decimal("75.00"), vat_percent=Decimal("0.0")
        )
        order2 = _create_order(user)
        _create_order_item(order2, product2, quantity=1)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            LoyaltyService.award_order_points(order2.id)
            bonus2 = LoyaltyService.check_new_customer_bonus(user, order2)

        assert bonus2 == 0

        # Only one BONUS transaction total
        assert (
            PointsTransaction.objects.filter(
                user=user, transaction_type=TransactionType.BONUS
            ).count()
            == 1
        )

    def test_bonus_not_awarded_when_disabled(self):
        """No bonus is awarded when LOYALTY_NEW_CUSTOMER_BONUS_ENABLED is false."""
        user = UserAccountFactory()
        product = _create_product(
            price=Decimal("100.00"), vat_percent=Decimal("0.0")
        )
        order = _create_order(user)
        _create_order_item(order, product, quantity=1)

        mock_settings = _loyalty_settings(
            enabled=True,
            points_factor=1.0,
            new_customer_bonus_enabled=False,
        )

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            LoyaltyService.award_order_points(order.id)
            bonus = LoyaltyService.check_new_customer_bonus(user, order)

        assert bonus == 0
        assert (
            PointsTransaction.objects.filter(
                user=user, transaction_type=TransactionType.BONUS
            ).count()
            == 0
        )
