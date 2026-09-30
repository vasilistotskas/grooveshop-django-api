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
from order.enum.status import OrderStatus, PaymentStatus
from tests.utils.loyalty import loyalty_settings

pytestmark = pytest.mark.assert_english


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

    def _order_with_a_line(self, user):
        from djmoney.money import Money

        from order.factories.item import OrderItemFactory
        from order.factories.order import OrderFactory
        from product.factories import ProductFactory

        order = OrderFactory(
            user=user,
            num_order_items=0,
            status=OrderStatus.COMPLETED,
            payment_status=PaymentStatus.COMPLETED,
        )
        product = ProductFactory(
            price=Money(Decimal("10.00"), "EUR"),
            discount_percent=Decimal(0),
            vat=None,
            points_coefficient=Decimal("1.00"),
            points=0,
            num_images=0,
            num_reviews=0,
        )
        OrderItemFactory(
            order=order, product=product, quantity=2, price=product.price
        )
        return order

    def test_award_order_points_is_a_no_op(self):
        """A real user order with a priced line would earn points; the
        gate alone must stop it — no transaction, no XP."""
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory(num_addresses=0)
        order = self._order_with_a_line(user)
        user.refresh_from_db()
        xp_before = user.total_xp
        rows_before = PointsTransaction.objects.count()

        with loyalty_settings(LOYALTY_ENABLED=False):
            result = LoyaltyService.award_order_points(order.id)

        assert result == 0
        assert PointsTransaction.objects.count() == rows_before
        assert not PointsTransaction.objects.filter(
            reference_order=order
        ).exists()
        user.refresh_from_db()
        assert user.total_xp == xp_before

    def test_reverse_order_points_is_a_no_op(self):
        """An order with EARN rows would be reversed; disabled, its
        points and the XP they gave stay exactly where they are."""
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory(num_addresses=0)
        order = self._order_with_a_line(user)
        PointsTransaction.objects.create(
            user=user,
            points=20,
            transaction_type=TransactionType.EARN,
            reference_order=order,
            description="Earned",
        )
        user.total_xp = 20
        user.save(update_fields=["total_xp"])

        with loyalty_settings(LOYALTY_ENABLED=False):
            result = LoyaltyService.reverse_order_points(order.id)

        assert result == 0
        assert not PointsTransaction.objects.filter(
            transaction_type=TransactionType.ADJUST
        ).exists()
        assert LoyaltyService.get_user_balance(user) == 20
        user.refresh_from_db()
        assert user.total_xp == 20

    def test_redeem_points_raises_validation_error(self):
        """redeem_points raises ValidationError when disabled."""
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()

        with (
            patch(
                "loyalty.services.Setting.get",
                side_effect=self._mock_settings_disabled,
            ),
            pytest.raises(ValidationError, match="disabled"),
        ):
            LoyaltyService.redeem_points(
                user, 100, "EUR", max_discount=Decimal(99999)
            )

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


@pytest.mark.django_db
class TestBalanceClampingOnExpiration:
    """Expiration must not drive the balance negative when points were spent."""

    def test_expiration_clamps_to_available_balance(self):
        from datetime import timedelta

        from django.utils import timezone

        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()

        earn = PointsTransaction.objects.create(
            user=user,
            points=100,
            transaction_type=TransactionType.EARN,
            description="Earned",
        )
        # Backdate past the expiration cutoff.
        PointsTransaction.objects.filter(pk=earn.pk).update(
            created_at=timezone.now() - timedelta(days=400)
        )
        # User already spent 80, so only 20 remain.
        PointsTransaction.objects.create(
            user=user,
            points=-80,
            transaction_type=TransactionType.REDEEM,
            description="Redeemed",
        )
        assert LoyaltyService.get_user_balance(user) == 20

        def mock_settings(key, default=None):
            return {"LOYALTY_POINTS_EXPIRATION_DAYS": 365}.get(key, default)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            count = LoyaltyService.process_expiration()

        assert count == 1
        # Clamped to the 20 still available, never negative.
        assert LoyaltyService.get_user_balance(user) == 0
        expire = PointsTransaction.objects.get(
            user=user, transaction_type=TransactionType.EXPIRE
        )
        assert expire.points == -20


@pytest.mark.django_db
class TestRedeemClawbackOnReversal:
    """Cancelling an order restores the points the customer redeemed on it."""

    def test_reversal_restores_redeemed_points(self):
        from order.models.order import Order
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()
        order = Order.objects.create(user=user)

        # Earned 100 on this order, redeemed 50 against it (balance 50).
        PointsTransaction.objects.create(
            user=user,
            points=100,
            transaction_type=TransactionType.EARN,
            reference_order=order,
            description="Earned",
        )
        PointsTransaction.objects.create(
            user=user,
            points=-50,
            transaction_type=TransactionType.REDEEM,
            reference_order=order,
            description="Redeemed on order",
        )
        assert LoyaltyService.get_user_balance(user) == 50

        def mock_settings(key, default=None):
            return {"LOYALTY_ENABLED": True}.get(key, default)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            LoyaltyService.reverse_order_points(order.id)

        # Earn fully reversed (restore happens first, so no clamp) and the 50
        # redeemed points are credited back — net balance returns to 0.
        assert LoyaltyService.get_user_balance(user) == 0
        assert PointsTransaction.objects.filter(
            user=user,
            transaction_type=TransactionType.ADJUST,
            points=50,
            description__contains="Redeemed points restored",
        ).exists()


@pytest.mark.django_db
class TestReversalOfAnOrderWithoutLoyaltyActivity:
    """An order that earned, redeemed and granted nothing has nothing to
    reverse — the customer's other points and XP stay untouched."""

    def test_returns_zero_and_writes_nothing(self):
        from order.models.order import Order
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()
        user.total_xp = 500
        user.save(update_fields=["total_xp"])
        earning_order = Order.objects.create(user=user)
        quiet_order = Order.objects.create(user=user)
        PointsTransaction.objects.create(
            user=user,
            points=100,
            transaction_type=TransactionType.EARN,
            reference_order=earning_order,
            description="Earned on another order",
        )

        def mock_settings(key, default=None):
            return {"LOYALTY_ENABLED": True}.get(key, default)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            reversed_amount = LoyaltyService.reverse_order_points(
                quiet_order.id
            )

        assert reversed_amount == 0
        assert not PointsTransaction.objects.filter(
            transaction_type=TransactionType.ADJUST
        ).exists()
        assert LoyaltyService.get_user_balance(user) == 100
        user.refresh_from_db()
        assert user.total_xp == 500


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
                LoyaltyService.redeem_points(
                    user, 100, "EUR", max_discount=Decimal(99999)
                )

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
            discount = LoyaltyService.redeem_points(
                user, 50, "EUR", max_discount=Decimal(99999)
            )

        assert discount == Decimal(50) / Decimal(100)
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
                LoyaltyService.redeem_points(
                    user, 50, "GBP", max_discount=Decimal(99999)
                )


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
                LoyaltyService.redeem_points(
                    user, -10, "EUR", max_discount=Decimal(99999)
                )

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
                LoyaltyService.redeem_points(
                    user, 0, "EUR", max_discount=Decimal(99999)
                )


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
        order = OrderFactory(
            user=user,
            num_order_items=0,
            status=OrderStatus.COMPLETED,
            payment_status=PaymentStatus.COMPLETED,
        )

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
                user, 150, "EUR", max_discount=Decimal(99999), order=order
            )

        # Verify discount calculation
        assert discount == Decimal(150) / Decimal(100)

        # Verify metadata stored in order
        order.refresh_from_db()
        assert order.metadata["loyalty_points_redeemed"] == 150
        assert order.metadata["loyalty_discount"] == str(
            Decimal(150) / Decimal(100)
        )

    def test_redeem_with_order_sets_reference_order_on_transaction(self):
        """REDEEM transaction references the order when order is provided."""
        from order.factories.order import OrderFactory
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()
        order = OrderFactory(
            user=user,
            num_order_items=0,
            status=OrderStatus.COMPLETED,
            payment_status=PaymentStatus.COMPLETED,
        )

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
            LoyaltyService.redeem_points(
                user, 50, "EUR", max_discount=Decimal(99999), order=order
            )

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
            discount = LoyaltyService.redeem_points(
                user, 50, "EUR", max_discount=Decimal(99999)
            )

        assert discount == Decimal(50) / Decimal(100)

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
        order = OrderFactory(
            user=user,
            num_order_items=0,
            status=OrderStatus.COMPLETED,
            payment_status=PaymentStatus.COMPLETED,
        )

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
                user, 200, "USD", max_discount=Decimal(99999), order=order
            )

        expected_discount = Decimal(200) / Decimal(50)
        assert discount == expected_discount

        order.refresh_from_db()
        assert order.metadata["loyalty_points_redeemed"] == 200
        assert order.metadata["loyalty_discount"] == str(expected_discount)
