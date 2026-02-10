"""Property-based tests for LoyaltyService core calculations.

Feature: ranking-and-loyalty-points
Tests Properties 2, 3, 8, 11, 16, 17 from the design document.
"""

import math
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from loyalty.enum import PriceBasis, TransactionType
from loyalty.services import LoyaltyService


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Decimal strategy for monetary amounts (positive, reasonable range)
money_amounts = st.decimals(
    min_value=Decimal("0.00"),
    max_value=Decimal("99999.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

positive_money_amounts = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("99999.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Strategy for points_coefficient (0.00 to 99.99)
coefficients = st.decimals(
    min_value=Decimal("0.00"),
    max_value=Decimal("99.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Strategy for points_factor (positive)
points_factors = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("100.00"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Strategy for fixed bonus points
fixed_points = st.integers(min_value=0, max_value=10000)

# Strategy for quantity (positive)
quantities = st.integers(min_value=1, max_value=100)

# Strategy for price basis enum
price_basis_choices = st.sampled_from(list(PriceBasis))

# Strategy for XP values
xp_values = st.integers(min_value=0, max_value=10_000_000)

# Strategy for XP per level (positive)
xp_per_level_values = st.integers(min_value=1, max_value=100_000)

# Strategy for redemption ratio (positive)
redemption_ratios = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("10000.00"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Strategy for points to redeem (positive)
redeem_points_amounts = st.integers(min_value=1, max_value=100_000)

# Strategy for transaction points (signed)
transaction_points = st.integers(min_value=-10000, max_value=10000)

# Strategy for distinct required levels (sorted list of unique positive ints)
tier_levels = st.lists(
    st.integers(min_value=1, max_value=1000),
    min_size=1,
    max_size=10,
    unique=True,
)


# ---------------------------------------------------------------------------
# Helper: build a mock product with money-like attributes
# ---------------------------------------------------------------------------


def _make_mock_product(
    price: Decimal,
    vat_value: Decimal,
    discount_value: Decimal,
    final_price: Decimal,
    points_coefficient: Decimal = Decimal("1.00"),
    bonus_points: int = 0,
) -> MagicMock:
    """Create a mock product with .price.amount, .vat_value.amount, etc."""
    product = MagicMock()
    product.price.amount = price
    product.vat_value.amount = vat_value
    product.discount_value.amount = discount_value
    product.final_price.amount = final_price
    product.points_coefficient = points_coefficient
    product.points = bonus_points
    return product


# ===========================================================================
# Property 2: Price basis calculation correctness
# Feature: ranking-and-loyalty-points, Property 2: Price basis calculation correctness
# ===========================================================================


class TestPriceBasisCalculation:
    """**Validates: Requirements 1.3, 1.4**"""

    @given(
        price=positive_money_amounts,
        vat_value=money_amounts,
        discount_value=money_amounts,
        final_price=positive_money_amounts,
        basis=price_basis_choices,
    )
    @settings(max_examples=100)
    def test_price_basis_returns_correct_value_for_all_settings(
        self,
        price: Decimal,
        vat_value: Decimal,
        discount_value: Decimal,
        final_price: Decimal,
        basis: str,
    ):
        """For any product and any LOYALTY_PRICE_BASIS setting,
        get_price_basis_amount returns the correct price component.

        **Validates: Requirements 1.3, 1.4**
        """
        product = _make_mock_product(
            price, vat_value, discount_value, final_price
        )

        with patch(
            "loyalty.services.Setting.get",
            return_value=basis,
        ):
            result = LoyaltyService.get_price_basis_amount(product)

        if basis == PriceBasis.PRICE_EXCL_VAT_NO_DISCOUNT:
            expected = Decimal(str(price))
        elif basis == PriceBasis.PRICE_EXCL_VAT_WITH_DISCOUNT:
            expected = Decimal(str(price)) - Decimal(str(discount_value))
        elif basis == PriceBasis.PRICE_INCL_VAT_NO_DISCOUNT:
            expected = Decimal(str(price)) + Decimal(str(vat_value))
        else:  # FINAL_PRICE
            expected = Decimal(str(final_price))

        assert result == expected, (
            f"basis={basis}, price={price}, vat={vat_value}, "
            f"discount={discount_value}, final={final_price}: "
            f"expected {expected}, got {result}"
        )


# ===========================================================================
# Property 3: Points calculation formula correctness
# Feature: ranking-and-loyalty-points, Property 3: Points calculation formula correctness
# ===========================================================================


class TestPointsCalculationFormula:
    """**Validates: Requirements 3.1, 2.3**"""

    @given(
        price=positive_money_amounts,
        points_coefficient=coefficients,
        bonus_points=fixed_points,
        points_factor=points_factors,
        quantity=quantities,
    )
    @settings(max_examples=100)
    def test_points_formula_matches_specification(
        self,
        price: Decimal,
        points_coefficient: Decimal,
        bonus_points: int,
        points_factor: Decimal,
        quantity: int,
    ):
        """For any product and positive points_factor and quantity,
        calculated points = floor(price_basis * factor * coefficient * quantity)
                          + (product.points * quantity).

        When points_coefficient is 0.0, the calculated portion is zero
        but fixed points are still awarded.

        **Validates: Requirements 3.1, 2.3**
        """
        product = _make_mock_product(
            price=price,
            vat_value=Decimal("0.00"),
            discount_value=Decimal("0.00"),
            final_price=price,
            points_coefficient=points_coefficient,
            bonus_points=bonus_points,
        )

        def mock_setting_get(key, default=None):
            settings_map = {
                "LOYALTY_POINTS_FACTOR": float(points_factor),
                "LOYALTY_PRICE_BASIS": "final_price",
                "LOYALTY_TIER_MULTIPLIER_ENABLED": False,
            }
            return settings_map.get(key, default)

        with patch(
            "loyalty.services.Setting.get", side_effect=mock_setting_get
        ):
            result = LoyaltyService.calculate_item_points(
                product, quantity, tier_multiplier=Decimal("1.0")
            )

        # Expected: floor(price_basis * factor * coefficient * quantity) + (bonus * quantity)
        price_basis = Decimal(str(price))  # final_price basis
        expected_calculated = (
            price_basis
            * Decimal(str(points_factor))
            * Decimal(str(points_coefficient))
            * quantity
        )
        expected = math.floor(expected_calculated) + (bonus_points * quantity)

        assert result == expected, (
            f"price={price}, coeff={points_coefficient}, bonus={bonus_points}, "
            f"factor={points_factor}, qty={quantity}: expected {expected}, got {result}"
        )


# ===========================================================================
# Property 8: Redemption converts points to discount correctly
# Feature: ranking-and-loyalty-points, Property 8: Redemption converts points to discount correctly
# ===========================================================================


@pytest.mark.django_db
class TestRedemptionDiscount:
    """**Validates: Requirements 4.1**"""

    @given(
        points_amount=redeem_points_amounts,
        redemption_ratio=redemption_ratios,
    )
    @settings(max_examples=100, deadline=500)
    def test_redemption_returns_correct_discount(
        self,
        points_amount: int,
        redemption_ratio: Decimal,
    ):
        """For any positive points_amount and positive redemption_ratio,
        redeem_points returns discount = points_amount / redemption_ratio.

        **Validates: Requirements 4.1**
        """
        from user.factories.account import UserAccountFactory

        from loyalty.models.transaction import PointsTransaction

        user = UserAccountFactory()

        # Give the user enough balance by creating an EARN transaction
        PointsTransaction.objects.create(
            user=user,
            points=points_amount,
            transaction_type=TransactionType.EARN,
            description="Test balance setup",
        )

        def mock_setting_get(key, default=None):
            settings_map = {
                "LOYALTY_ENABLED": True,
                "LOYALTY_REDEMPTION_RATIO_EUR": float(redemption_ratio),
            }
            return settings_map.get(key, default)

        with patch(
            "loyalty.services.Setting.get", side_effect=mock_setting_get
        ):
            discount = LoyaltyService.redeem_points(user, points_amount, "EUR")

        expected_discount = Decimal(str(points_amount)) / Decimal(
            str(redemption_ratio)
        )

        assert discount == expected_discount, (
            f"points={points_amount}, ratio={redemption_ratio}: "
            f"expected {expected_discount}, got {discount}"
        )

        # Clean up for next hypothesis example
        PointsTransaction.objects.filter(user=user).delete()
        user.delete()


# ===========================================================================
# Property 11: Balance equals sum of all transactions
# Feature: ranking-and-loyalty-points, Property 11: Balance equals sum of all transactions
# ===========================================================================


@pytest.mark.django_db
class TestBalanceEqualsTransactionSum:
    """**Validates: Requirements 5.2**"""

    @given(
        points_list=st.lists(
            transaction_points,
            min_size=0,
            max_size=20,
        ),
    )
    @settings(max_examples=100, deadline=1000)
    def test_balance_equals_algebraic_sum_of_transactions(
        self,
        points_list: list[int],
    ):
        """For any user and any sequence of transactions,
        get_balance(user) equals the algebraic sum of all
        PointsTransaction.points values.

        **Validates: Requirements 5.2**
        """
        from user.factories.account import UserAccountFactory

        from loyalty.models.transaction import PointsTransaction

        user = UserAccountFactory()

        tx_types = [
            TransactionType.EARN,
            TransactionType.REDEEM,
            TransactionType.EXPIRE,
            TransactionType.ADJUST,
            TransactionType.BONUS,
        ]

        for i, pts in enumerate(points_list):
            PointsTransaction.objects.create(
                user=user,
                points=pts,
                transaction_type=tx_types[i % len(tx_types)],
                description=f"Test transaction {i}",
            )

        balance = LoyaltyService.get_user_balance(user)
        expected = sum(points_list)

        assert balance == expected, (
            f"transactions={points_list}: expected sum={expected}, got balance={balance}"
        )

        # Clean up for next hypothesis example
        PointsTransaction.objects.filter(user=user).delete()
        user.delete()


# ===========================================================================
# Property 16: Level formula correctness
# Feature: ranking-and-loyalty-points, Property 16: Level formula correctness
# ===========================================================================


class TestLevelFormula:
    """**Validates: Requirements 8.2**"""

    @given(
        total_xp=xp_values,
        xp_per_level=xp_per_level_values,
    )
    @settings(max_examples=100)
    def test_level_equals_one_plus_floor_xp_div_xp_per_level(
        self,
        total_xp: int,
        xp_per_level: int,
    ):
        """For any non-negative total_xp and positive XP_PER_LEVEL,
        level = 1 + floor(total_xp / XP_PER_LEVEL).

        **Validates: Requirements 8.2**
        """
        user = MagicMock()
        user.total_xp = total_xp

        with patch(
            "loyalty.services.Setting.get",
            return_value=xp_per_level,
        ):
            level = LoyaltyService.get_user_level(user)

        expected = 1 + (total_xp // xp_per_level)

        assert level == expected, (
            f"total_xp={total_xp}, xp_per_level={xp_per_level}: "
            f"expected level={expected}, got {level}"
        )


# ===========================================================================
# Property 17: Tier assignment picks highest qualifying
# Feature: ranking-and-loyalty-points, Property 17: Tier assignment picks highest qualifying
# ===========================================================================


@pytest.mark.django_db
class TestTierAssignment:
    """**Validates: Requirements 8.5**"""

    @given(
        required_levels=tier_levels,
        user_level=st.integers(min_value=0, max_value=1500),
    )
    @settings(max_examples=100)
    def test_tier_is_highest_qualifying_for_level(
        self,
        required_levels: list[int],
        user_level: int,
    ):
        """For any set of LoyaltyTier records with distinct required_levels
        and any user level L, the assigned tier is the tier with the highest
        required_level <= L. If no tier qualifies, the result is None.

        **Validates: Requirements 8.5**
        """
        from loyalty.models.tier import LoyaltyTier

        # Clean up any existing tiers from previous examples
        LoyaltyTier.objects.all().delete()

        # Create tiers with distinct required_levels
        created_tiers = []
        for lvl in required_levels:
            tier = LoyaltyTier.objects.create(
                required_level=lvl,
                points_multiplier=Decimal("1.00"),
            )
            created_tiers.append(tier)

        # Use the manager method directly (same as get_user_tier uses)
        result = LoyaltyTier.objects.get_for_level(user_level)

        # Compute expected: highest required_level <= user_level
        qualifying = [lvl for lvl in required_levels if lvl <= user_level]
        if qualifying:
            expected_level = max(qualifying)
            assert result is not None, (
                f"levels={required_levels}, user_level={user_level}: "
                f"expected tier with required_level={expected_level}, got None"
            )
            assert result.required_level == expected_level, (
                f"levels={required_levels}, user_level={user_level}: "
                f"expected required_level={expected_level}, got {result.required_level}"
            )
        else:
            assert result is None, (
                f"levels={required_levels}, user_level={user_level}: "
                f"expected None, got tier with required_level={result.required_level}"
            )

        # Clean up
        LoyaltyTier.objects.all().delete()
