"""Unit tests for LoyaltyService edge cases.

Tests cover: disabled system, zero coefficient, zero XP_PER_LEVEL,
empty transactions, balance clamping on reversal, over-redemption rejection,
invalid currency, and negative points_amount.

Requirements: 1.2, 2.3, 4.2, 6.4
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.core.exceptions import ValidationError

from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction
from loyalty.services import LoyaltyService


# ---------------------------------------------------------------------------
# Helper: build a mock product
# ---------------------------------------------------------------------------


def _make_mock_product(
    price: Decimal = Decimal("50.00"),
    vat_value: Decimal = Decimal("10.00"),
    discount_value: Decimal = Decimal("5.00"),
    final_price: Decimal = Decimal("55.00"),
    points_coefficient: Decimal = Decimal("1.00"),
    bonus_points: int = 0,
) -> MagicMock:
    product = MagicMock()
    product.price.amount = price
    product.vat_value.amount = vat_value
    product.discount_value.amount = discount_value
    product.final_price.amount = final_price
    product.points_coefficient = points_coefficient
    product.points = bonus_points
    return product


# ===========================================================================
# 1. Disabled system — LOYALTY_ENABLED is false
# Validates: Requirement 1.2
# ===========================================================================


@pytest.mark.django_db
class TestDisabledSystem:
    """When LOYALTY_ENABLED is false, all operations should be no-ops or raise."""

    def _mock_settings_disabled(self, key, default=None):
        settings_map = {
            "LOYALTY_ENABLED": False,
        }
        return settings_map.get(key, default)

    def test_award_order_points_returns_zero(self):
        """award_order_points returns 0 and creates no transactions."""
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()

        with patch(
            "loyalty.services.Setting.get",
            side_effect=self._mock_settings_disabled,
        ):
            result = LoyaltyService.award_order_points(order_id=999)

        assert result == 0
        assert PointsTransaction.objects.filter(user=user).count() == 0

    def test_reverse_order_points_returns_zero(self):
        """reverse_order_points returns 0 when disabled."""
        with patch(
            "loyalty.services.Setting.get",
            side_effect=self._mock_settings_disabled,
        ):
            result = LoyaltyService.reverse_order_points(order_id=999)

        assert result == 0

    def test_redeem_points_raises_validation_error(self):
        """redeem_points raises ValidationError when disabled."""
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()

        with patch(
            "loyalty.services.Setting.get",
            side_effect=self._mock_settings_disabled,
        ):
            with pytest.raises(ValidationError, match="disabled"):
                LoyaltyService.redeem_points(user, 100, "EUR")

    def test_get_product_potential_points_returns_zero(self):
        """get_product_potential_points returns 0 when disabled."""
        product = _make_mock_product()

        with patch(
            "loyalty.services.Setting.get",
            side_effect=self._mock_settings_disabled,
        ):
            result = LoyaltyService.get_product_potential_points(product)

        assert result == 0


# ===========================================================================
# 2. Zero coefficient — product.points_coefficient = 0.0
# Validates: Requirement 2.3
# ===========================================================================


class TestZeroCoefficient:
    """When points_coefficient is 0.0, calculated portion is zero but fixed points still awarded."""

    def test_zero_coefficient_awards_only_fixed_points(self):
        """With coefficient=0.0, only product.points are awarded."""
        product = _make_mock_product(
            price=Decimal("100.00"),
            final_price=Decimal("100.00"),
            points_coefficient=Decimal("0.00"),
            bonus_points=25,
        )

        def mock_settings(key, default=None):
            settings_map = {
                "LOYALTY_POINTS_FACTOR": 1.0,
                "LOYALTY_PRICE_BASIS": "final_price",
                "LOYALTY_TIER_MULTIPLIER_ENABLED": False,
            }
            return settings_map.get(key, default)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            result = LoyaltyService.calculate_item_points(
                product, quantity=1, tier_multiplier=Decimal("1.0")
            )

        assert result == 25

    def test_zero_coefficient_with_quantity(self):
        """With coefficient=0.0 and quantity>1, fixed points scale by quantity."""
        product = _make_mock_product(
            price=Decimal("100.00"),
            final_price=Decimal("100.00"),
            points_coefficient=Decimal("0.00"),
            bonus_points=10,
        )

        def mock_settings(key, default=None):
            settings_map = {
                "LOYALTY_POINTS_FACTOR": 1.0,
                "LOYALTY_PRICE_BASIS": "final_price",
                "LOYALTY_TIER_MULTIPLIER_ENABLED": False,
            }
            return settings_map.get(key, default)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            result = LoyaltyService.calculate_item_points(
                product, quantity=3, tier_multiplier=Decimal("1.0")
            )

        assert result == 30  # 0 calculated + 10 * 3 fixed


# ===========================================================================
# 3. Zero XP_PER_LEVEL — setting is 0 or negative
# Validates: Requirement 8.2 (safe default)
# ===========================================================================


class TestZeroXPPerLevel:
    """When XP_PER_LEVEL is 0 or negative, get_user_level returns 1."""

    def test_zero_xp_per_level_returns_level_one(self):
        user = MagicMock()
        user.total_xp = 5000

        with patch("loyalty.services.Setting.get", return_value=0):
            level = LoyaltyService.get_user_level(user)

        assert level == 1

    def test_negative_xp_per_level_returns_level_one(self):
        user = MagicMock()
        user.total_xp = 5000

        with patch("loyalty.services.Setting.get", return_value=-100):
            level = LoyaltyService.get_user_level(user)

        assert level == 1


# ===========================================================================
# 4. Empty transactions — user with no transactions
# Validates: Requirement 5.2
# ===========================================================================


@pytest.mark.django_db
class TestEmptyTransactions:
    """User with no transactions has balance of 0."""

    def test_get_user_balance_returns_zero(self):
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()
        balance = LoyaltyService.get_user_balance(user)

        assert balance == 0


# ===========================================================================
# 5. Balance clamping on reversal
# Validates: Requirement 6.4
# ===========================================================================


@pytest.mark.django_db
class TestBalanceClampingOnReversal:
    """When reversal would cause negative balance, balance is clamped to 0."""

    def test_reversal_clamps_balance_to_zero(self):
        """If user redeemed some points, reversal should not go negative."""
        from order.models.order import Order
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()

        # Create a real order for the user
        order = Order.objects.create(user=user)

        # User earned 100 points from this order
        PointsTransaction.objects.create(
            user=user,
            points=100,
            transaction_type=TransactionType.EARN,
            reference_order=order,
            description="Earned from order",
        )

        # User redeemed 80 points (balance is now 20)
        PointsTransaction.objects.create(
            user=user,
            points=-80,
            transaction_type=TransactionType.REDEEM,
            description="Redeemed points",
        )

        assert LoyaltyService.get_user_balance(user) == 20

        def mock_settings(key, default=None):
            settings_map = {
                "LOYALTY_ENABLED": True,
            }
            return settings_map.get(key, default)

        # Now reverse the order — should clamp to 0, not go to -80
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            reversed_amount = LoyaltyService.reverse_order_points(order.id)

        # Only 20 should be reversed (clamped from 100 to 20)
        assert reversed_amount == 20
        assert LoyaltyService.get_user_balance(user) == 0


# ===========================================================================
# 6. Over-redemption rejection
# Validates: Requirement 4.2
# ===========================================================================


@pytest.mark.django_db
class TestOverRedemptionRejection:
    """When user tries to redeem more than balance, ValidationError is raised."""

    def test_redeem_more_than_balance_raises_error(self):
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()

        # Give user 50 points
        PointsTransaction.objects.create(
            user=user,
            points=50,
            transaction_type=TransactionType.EARN,
            description="Test earn",
        )

        def mock_settings(key, default=None):
            settings_map = {
                "LOYALTY_ENABLED": True,
                "LOYALTY_REDEMPTION_RATIO_EUR": 100.0,
            }
            return settings_map.get(key, default)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            with pytest.raises(
                ValidationError, match="Insufficient points balance"
            ):
                LoyaltyService.redeem_points(user, 100, "EUR")

        # No REDEEM transaction should have been created
        assert not PointsTransaction.objects.filter(
            user=user, transaction_type=TransactionType.REDEEM
        ).exists()

    def test_redeem_exact_balance_succeeds(self):
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()

        PointsTransaction.objects.create(
            user=user,
            points=50,
            transaction_type=TransactionType.EARN,
            description="Test earn",
        )

        def mock_settings(key, default=None):
            settings_map = {
                "LOYALTY_ENABLED": True,
                "LOYALTY_REDEMPTION_RATIO_EUR": 100.0,
            }
            return settings_map.get(key, default)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            discount = LoyaltyService.redeem_points(user, 50, "EUR")

        assert discount == Decimal("50") / Decimal("100")
        assert LoyaltyService.get_user_balance(user) == 0


# ===========================================================================
# 7. Invalid currency in redemption
# Validates: Requirement 4.2
# ===========================================================================


@pytest.mark.django_db
class TestInvalidCurrency:
    """Unsupported currency raises ValidationError."""

    def test_unsupported_currency_raises_error(self):
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()

        PointsTransaction.objects.create(
            user=user,
            points=100,
            transaction_type=TransactionType.EARN,
            description="Test earn",
        )

        def mock_settings(key, default=None):
            settings_map = {
                "LOYALTY_ENABLED": True,
            }
            return settings_map.get(key, default)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            with pytest.raises(ValidationError, match="Unsupported currency"):
                LoyaltyService.redeem_points(user, 50, "GBP")


# ===========================================================================
# 8. Negative points_amount in redemption
# Validates: Requirement 4.2
# ===========================================================================


@pytest.mark.django_db
class TestNegativePointsAmount:
    """Negative or zero points_amount raises ValidationError."""

    def test_negative_points_raises_error(self):
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()

        def mock_settings(key, default=None):
            settings_map = {
                "LOYALTY_ENABLED": True,
            }
            return settings_map.get(key, default)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            with pytest.raises(
                ValidationError, match="Points amount must be positive"
            ):
                LoyaltyService.redeem_points(user, -10, "EUR")

    def test_zero_points_raises_error(self):
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()

        def mock_settings(key, default=None):
            settings_map = {
                "LOYALTY_ENABLED": True,
            }
            return settings_map.get(key, default)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            with pytest.raises(
                ValidationError, match="Points amount must be positive"
            ):
                LoyaltyService.redeem_points(user, 0, "EUR")


# ===========================================================================
# 9. Order metadata integration for redemption
# Validates: Requirement 4.5
# ===========================================================================


@pytest.mark.django_db
class TestOrderMetadataIntegration:
    """When redeem_points is called with an order, loyalty metadata is stored in Order.metadata."""

    def test_redeem_with_order_stores_metadata(self):
        """Redeeming points with an order stores loyalty_points_redeemed and loyalty_discount."""
        from order.factories.order import OrderFactory
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()
        order = OrderFactory(user=user, num_order_items=0)

        # Give user 200 points
        PointsTransaction.objects.create(
            user=user,
            points=200,
            transaction_type=TransactionType.EARN,
            description="Test earn",
        )

        def mock_settings(key, default=None):
            settings_map = {
                "LOYALTY_ENABLED": True,
                "LOYALTY_REDEMPTION_RATIO_EUR": 100.0,
            }
            return settings_map.get(key, default)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            discount = LoyaltyService.redeem_points(
                user, 150, "EUR", order=order
            )

        # Verify discount calculation
        assert discount == Decimal("150") / Decimal("100")

        # Verify metadata stored in order
        order.refresh_from_db()
        assert order.metadata["loyalty_points_redeemed"] == 150
        assert order.metadata["loyalty_discount"] == str(
            Decimal("150") / Decimal("100")
        )

    def test_redeem_with_order_sets_reference_order_on_transaction(self):
        """REDEEM transaction references the order when order is provided."""
        from order.factories.order import OrderFactory
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()
        order = OrderFactory(user=user, num_order_items=0)

        PointsTransaction.objects.create(
            user=user,
            points=100,
            transaction_type=TransactionType.EARN,
            description="Test earn",
        )

        def mock_settings(key, default=None):
            settings_map = {
                "LOYALTY_ENABLED": True,
                "LOYALTY_REDEMPTION_RATIO_EUR": 100.0,
            }
            return settings_map.get(key, default)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            LoyaltyService.redeem_points(user, 50, "EUR", order=order)

        redeem_tx = PointsTransaction.objects.get(
            user=user, transaction_type=TransactionType.REDEEM
        )
        assert redeem_tx.reference_order == order
        assert redeem_tx.points == -50

    def test_redeem_without_order_no_metadata_stored(self):
        """Redeeming without an order does not attempt to store metadata."""
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()

        PointsTransaction.objects.create(
            user=user,
            points=100,
            transaction_type=TransactionType.EARN,
            description="Test earn",
        )

        def mock_settings(key, default=None):
            settings_map = {
                "LOYALTY_ENABLED": True,
                "LOYALTY_REDEMPTION_RATIO_EUR": 100.0,
            }
            return settings_map.get(key, default)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            discount = LoyaltyService.redeem_points(user, 50, "EUR")

        assert discount == Decimal("50") / Decimal("100")

        # REDEEM transaction should have no reference_order
        redeem_tx = PointsTransaction.objects.get(
            user=user, transaction_type=TransactionType.REDEEM
        )
        assert redeem_tx.reference_order is None

    def test_redeem_with_order_usd_currency(self):
        """Metadata is stored correctly when redeeming with USD currency."""
        from order.factories.order import OrderFactory
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()
        order = OrderFactory(user=user, num_order_items=0)

        PointsTransaction.objects.create(
            user=user,
            points=500,
            transaction_type=TransactionType.EARN,
            description="Test earn",
        )

        def mock_settings(key, default=None):
            settings_map = {
                "LOYALTY_ENABLED": True,
                "LOYALTY_REDEMPTION_RATIO_USD": 50.0,
            }
            return settings_map.get(key, default)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            discount = LoyaltyService.redeem_points(
                user, 200, "USD", order=order
            )

        expected_discount = Decimal("200") / Decimal("50")
        assert discount == expected_discount

        order.refresh_from_db()
        assert order.metadata["loyalty_points_redeemed"] == 200
        assert order.metadata["loyalty_discount"] == str(expected_discount)
