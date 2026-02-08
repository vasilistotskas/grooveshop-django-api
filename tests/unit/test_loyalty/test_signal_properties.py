"""Property-based tests for signal-driven loyalty flows.

Feature: ranking-and-loyalty-points
Tests Properties 1, 4, 5, 6, 7, 13, 14, 15, 18 from the design document.

**Validates: Requirements 1.2, 3.1-3.5, 6.1-6.4, 7.1-7.4, 9.1-9.3**
"""

import math
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone
from djmoney.money import Money
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction
from loyalty.services import LoyaltyService


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Monetary amounts for product prices (positive, reasonable range)
positive_prices = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("9999.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# VAT percentages (0-100)
vat_percents = st.decimals(
    min_value=Decimal("0.0"),
    max_value=Decimal("50.0"),
    places=1,
    allow_nan=False,
    allow_infinity=False,
)

# Discount percentages (0-100)
discount_percents = st.decimals(
    min_value=Decimal("0.0"),
    max_value=Decimal("50.0"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Points coefficient (0.00 to 10.00)
coefficients = st.decimals(
    min_value=Decimal("0.00"),
    max_value=Decimal("10.00"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Points factor (positive)
points_factors = st.decimals(
    min_value=Decimal("0.10"),
    max_value=Decimal("10.00"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Fixed bonus points per product
fixed_points = st.integers(min_value=0, max_value=100)

# Quantity per order item
quantities = st.integers(min_value=1, max_value=10)

# Number of order items (1-5 to keep tests fast)
num_items = st.integers(min_value=1, max_value=5)

# Tier multiplier (> 1.0)
tier_multipliers = st.decimals(
    min_value=Decimal("1.01"),
    max_value=Decimal("5.00"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Expiration days
expiration_days_positive = st.integers(min_value=1, max_value=365)

# Bonus points for new customer
bonus_points_values = st.integers(min_value=1, max_value=1000)


# ---------------------------------------------------------------------------
# Helpers: create real DB objects for testing
# ---------------------------------------------------------------------------


def _create_product(
    price: Decimal,
    vat_percent: Decimal = Decimal("0.0"),
    discount_percent: Decimal = Decimal("0.0"),
    points_coefficient: Decimal = Decimal("1.00"),
    bonus_points: int = 0,
):
    """Create a real Product with a Vat record in the database."""
    from vat.models import Vat
    from product.models.product import Product

    vat = None
    if vat_percent > 0:
        vat = Vat.objects.create(value=vat_percent)

    product = Product.objects.create(
        price=Money(price, "EUR"),
        discount_percent=discount_percent,
        vat=vat,
        points_coefficient=points_coefficient,
        points=bonus_points,
        stock=100,
        active=True,
    )
    return product


def _create_order(user=None):
    """Create a real Order in the database."""
    from order.models.order import Order

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
    """Create a real OrderItem in the database."""
    from order.models.item import OrderItem

    return OrderItem.objects.create(
        order=order,
        product=product,
        price=product.price,
        quantity=quantity,
    )


def _loyalty_settings(
    enabled=True,
    points_factor=1.0,
    price_basis="final_price",
    tier_multiplier_enabled=False,
    expiration_days=0,
    new_customer_bonus_enabled=False,
    new_customer_bonus_points=100,
):
    """Return a mock side_effect for Setting.get() with loyalty settings."""
    settings_map = {
        "LOYALTY_ENABLED": enabled,
        "LOYALTY_POINTS_FACTOR": points_factor,
        "LOYALTY_PRICE_BASIS": price_basis,
        "LOYALTY_TIER_MULTIPLIER_ENABLED": tier_multiplier_enabled,
        "LOYALTY_POINTS_EXPIRATION_DAYS": expiration_days,
        "LOYALTY_NEW_CUSTOMER_BONUS_ENABLED": new_customer_bonus_enabled,
        "LOYALTY_NEW_CUSTOMER_BONUS_POINTS": new_customer_bonus_points,
        "LOYALTY_XP_PER_LEVEL": 1000,
    }

    def _get(key, default=None):
        return settings_map.get(key, default)

    return _get


def _cleanup_loyalty_data():
    """Clean up all loyalty-related data between hypothesis examples."""
    from loyalty.models.tier import LoyaltyTier
    from order.models.item import OrderItem
    from order.models.order import Order
    from product.models.product import Product
    from vat.models import Vat

    PointsTransaction.objects.all().delete()
    OrderItem.objects.all().delete()
    Order.objects.all().delete()
    Product.objects.all().delete()
    Vat.objects.all().delete()
    LoyaltyTier.objects.all().delete()


# ===========================================================================
# Property 1: Disabled system produces no side effects
# Feature: ranking-and-loyalty-points, Property 1: Disabled system produces no side effects
# ===========================================================================


@pytest.mark.django_db
class TestDisabledSystemNoSideEffects:
    """**Validates: Requirements 1.2**"""

    @given(
        price=positive_prices,
        n_items=num_items,
        quantity=quantities,
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_disabled_system_creates_no_transactions_and_no_xp_change(
        self,
        price: Decimal,
        n_items: int,
        quantity: int,
    ):
        """If LOYALTY_ENABLED is false, no PointsTransaction records shall be
        created and no user's total_xp shall change.

        **Validates: Requirements 1.2**
        """
        from user.factories.account import UserAccountFactory

        try:
            user = UserAccountFactory()
            original_xp = user.total_xp
            order = _create_order(user=user)

            for _ in range(n_items):
                product = _create_product(price=price)
                _create_order_item(order, product, quantity)

            with patch(
                "loyalty.services.Setting.get",
                side_effect=_loyalty_settings(enabled=False),
            ):
                result = LoyaltyService.award_order_points(order.id)

            assert result == 0, f"Expected 0 points, got {result}"

            tx_count = PointsTransaction.objects.filter(user=user).count()
            assert tx_count == 0, (
                f"Expected 0 transactions when disabled, got {tx_count}"
            )

            user.refresh_from_db()
            assert user.total_xp == original_xp, (
                f"XP changed from {original_xp} to {user.total_xp} when disabled"
            )
        finally:
            _cleanup_loyalty_data()


# ===========================================================================
# Property 4: EARN transaction creation per order item
# Feature: ranking-and-loyalty-points, Property 4: EARN transaction creation per order item
# ===========================================================================


@pytest.mark.django_db
class TestEarnTransactionPerItem:
    """**Validates: Requirements 3.2**"""

    @given(
        n_items=num_items,
        price=positive_prices,
        quantity=quantities,
        points_factor=points_factors,
        coeff=coefficients,
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_n_items_produce_n_earn_transactions(
        self,
        n_items: int,
        price: Decimal,
        quantity: int,
        points_factor: Decimal,
        coeff: Decimal,
    ):
        """For any completed order with N items belonging to an authenticated
        user, exactly N PointsTransaction records of type EARN shall be created,
        each referencing the order.

        **Validates: Requirements 3.2**
        """
        from user.factories.account import UserAccountFactory

        try:
            user = UserAccountFactory()
            order = _create_order(user=user)

            for _ in range(n_items):
                product = _create_product(
                    price=price,
                    points_coefficient=coeff,
                )
                _create_order_item(order, product, quantity)

            with patch(
                "loyalty.services.Setting.get",
                side_effect=_loyalty_settings(
                    enabled=True,
                    points_factor=float(points_factor),
                ),
            ):
                LoyaltyService.award_order_points(order.id)

            earn_txs = PointsTransaction.objects.filter(
                user=user,
                transaction_type=TransactionType.EARN,
                reference_order=order,
            )
            assert earn_txs.count() == n_items, (
                f"Expected {n_items} EARN transactions, got {earn_txs.count()}"
            )
        finally:
            _cleanup_loyalty_data()


# ===========================================================================
# Property 5: XP equals total earned points
# Feature: ranking-and-loyalty-points, Property 5: XP equals total earned points
# ===========================================================================


@pytest.mark.django_db
class TestXpEqualsTotalEarned:
    """**Validates: Requirements 3.3**"""

    @given(
        n_items=num_items,
        price=positive_prices,
        quantity=quantities,
        points_factor=points_factors,
        coeff=coefficients,
        bonus=fixed_points,
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_xp_added_equals_sum_of_earn_transactions(
        self,
        n_items: int,
        price: Decimal,
        quantity: int,
        points_factor: Decimal,
        coeff: Decimal,
        bonus: int,
    ):
        """The XP added to user's total_xp shall equal the sum of all EARN
        transaction points created for that order.

        **Validates: Requirements 3.3**
        """
        from user.factories.account import UserAccountFactory

        try:
            user = UserAccountFactory()
            initial_xp = user.total_xp
            order = _create_order(user=user)

            for _ in range(n_items):
                product = _create_product(
                    price=price,
                    points_coefficient=coeff,
                    bonus_points=bonus,
                )
                _create_order_item(order, product, quantity)

            with patch(
                "loyalty.services.Setting.get",
                side_effect=_loyalty_settings(
                    enabled=True,
                    points_factor=float(points_factor),
                ),
            ):
                total_awarded = LoyaltyService.award_order_points(order.id)

            # Sum of EARN transactions for this order
            from django.db.models import Sum

            earn_sum = (
                PointsTransaction.objects.filter(
                    user=user,
                    transaction_type=TransactionType.EARN,
                    reference_order=order,
                ).aggregate(total=Sum("points"))["total"]
                or 0
            )

            user.refresh_from_db()
            xp_added = user.total_xp - initial_xp

            assert xp_added == earn_sum, (
                f"XP added ({xp_added}) != sum of EARN transactions ({earn_sum})"
            )
            assert total_awarded == earn_sum, (
                f"Return value ({total_awarded}) != sum of EARN transactions ({earn_sum})"
            )
        finally:
            _cleanup_loyalty_data()


# ===========================================================================
# Property 6: Guest orders produce no transactions
# Feature: ranking-and-loyalty-points, Property 6: Guest orders produce no transactions
# ===========================================================================


@pytest.mark.django_db
class TestGuestOrdersNoTransactions:
    """**Validates: Requirements 3.4**"""

    @given(
        n_items=num_items,
        price=positive_prices,
        quantity=quantities,
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_guest_order_creates_zero_transactions(
        self,
        n_items: int,
        price: Decimal,
        quantity: int,
    ):
        """For any order where order.user is null, no PointsTransaction
        records shall be created.

        **Validates: Requirements 3.4**
        """
        try:
            # Guest order: user=None
            order = _create_order(user=None)

            for _ in range(n_items):
                product = _create_product(price=price)
                _create_order_item(order, product, quantity)

            with patch(
                "loyalty.services.Setting.get",
                side_effect=_loyalty_settings(enabled=True),
            ):
                result = LoyaltyService.award_order_points(order.id)

            assert result == 0, (
                f"Expected 0 points for guest order, got {result}"
            )

            tx_count = PointsTransaction.objects.filter(
                reference_order=order,
            ).count()
            assert tx_count == 0, (
                f"Expected 0 transactions for guest order, got {tx_count}"
            )
        finally:
            _cleanup_loyalty_data()


# ===========================================================================
# Property 7: Tier multiplier applied correctly
# Feature: ranking-and-loyalty-points, Property 7: Tier multiplier applied correctly
# ===========================================================================


@pytest.mark.django_db
class TestTierMultiplierApplied:
    """**Validates: Requirements 3.5**"""

    @given(
        price=positive_prices,
        coeff=st.decimals(
            min_value=Decimal("0.01"),
            max_value=Decimal("10.00"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
        points_factor=points_factors,
        multiplier=tier_multipliers,
        quantity=quantities,
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_tier_multiplier_scales_calculated_points(
        self,
        price: Decimal,
        coeff: Decimal,
        points_factor: Decimal,
        multiplier: Decimal,
        quantity: int,
    ):
        """When LOYALTY_TIER_MULTIPLIER_ENABLED is true and user has tier with
        multiplier M > 1.0, calculated points (excluding fixed) shall be M
        times the base. When disabled, multiplier is not applied.

        **Validates: Requirements 3.5**
        """
        from loyalty.models.tier import LoyaltyTier
        from user.factories.account import UserAccountFactory

        try:
            # Create tier with the given multiplier
            tier = LoyaltyTier.objects.create(
                required_level=1,
                points_multiplier=multiplier,
            )

            # Create user with this tier
            user = UserAccountFactory()
            user.loyalty_tier = tier
            user.save(update_fields=["loyalty_tier"])

            order = _create_order(user=user)
            product = _create_product(
                price=price,
                points_coefficient=coeff,
                bonus_points=0,  # No fixed points to isolate multiplier effect
            )
            _create_order_item(order, product, quantity)

            # --- Test with tier multiplier ENABLED ---
            with patch(
                "loyalty.services.Setting.get",
                side_effect=_loyalty_settings(
                    enabled=True,
                    points_factor=float(points_factor),
                    tier_multiplier_enabled=True,
                ),
            ):
                LoyaltyService.award_order_points(order.id)

            earn_with_mult = PointsTransaction.objects.filter(
                user=user,
                transaction_type=TransactionType.EARN,
                reference_order=order,
            ).first()
            assert earn_with_mult is not None

            # Expected with multiplier
            base_calc = (
                Decimal(str(price))
                * Decimal(str(points_factor))
                * Decimal(str(coeff))
                * quantity
            )
            expected_with = math.floor(base_calc * multiplier)
            assert earn_with_mult.points == expected_with, (
                f"With multiplier: expected {expected_with}, got {earn_with_mult.points}"
            )

            # Clean transactions for second test
            PointsTransaction.objects.filter(reference_order=order).delete()

            # --- Test with tier multiplier DISABLED ---
            with patch(
                "loyalty.services.Setting.get",
                side_effect=_loyalty_settings(
                    enabled=True,
                    points_factor=float(points_factor),
                    tier_multiplier_enabled=False,
                ),
            ):
                LoyaltyService.award_order_points(order.id)

            earn_without_mult = PointsTransaction.objects.filter(
                user=user,
                transaction_type=TransactionType.EARN,
                reference_order=order,
            ).first()
            assert earn_without_mult is not None

            # Expected without multiplier
            expected_without = math.floor(base_calc)
            assert earn_without_mult.points == expected_without, (
                f"Without multiplier: expected {expected_without}, got {earn_without_mult.points}"
            )
        finally:
            _cleanup_loyalty_data()


# ===========================================================================
# Property 13: Reversal negates original EARN sum
# Feature: ranking-and-loyalty-points, Property 13: Reversal negates original EARN sum
# ===========================================================================


@pytest.mark.django_db
class TestReversalNegatesEarnSum:
    """**Validates: Requirements 6.1, 6.2, 6.4**"""

    @given(
        n_items=num_items,
        price=positive_prices,
        quantity=quantities,
        points_factor=points_factors,
        coeff=st.decimals(
            min_value=Decimal("0.01"),
            max_value=Decimal("10.00"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_reversal_adjust_sum_negates_earn_sum(
        self,
        n_items: int,
        price: Decimal,
        quantity: int,
        points_factor: Decimal,
        coeff: Decimal,
    ):
        """Reversing an order creates ADJUST transactions whose sum equals -S
        where S is the total EARN sum. The net effect of EARN + ADJUST for
        that order shall be >= 0.

        **Validates: Requirements 6.1, 6.2, 6.4**
        """
        from django.db.models import Sum

        from user.factories.account import UserAccountFactory

        try:
            user = UserAccountFactory()
            order = _create_order(user=user)

            for _ in range(n_items):
                product = _create_product(
                    price=price,
                    points_coefficient=coeff,
                )
                _create_order_item(order, product, quantity)

            mock_settings = _loyalty_settings(
                enabled=True,
                points_factor=float(points_factor),
            )

            # Award points first
            with patch(
                "loyalty.services.Setting.get", side_effect=mock_settings
            ):
                LoyaltyService.award_order_points(order.id)

            earn_sum = (
                PointsTransaction.objects.filter(
                    user=user,
                    transaction_type=TransactionType.EARN,
                    reference_order=order,
                ).aggregate(total=Sum("points"))["total"]
                or 0
            )

            # Reverse the order
            with patch(
                "loyalty.services.Setting.get", side_effect=mock_settings
            ):
                LoyaltyService.reverse_order_points(order.id)

            adjust_sum = (
                PointsTransaction.objects.filter(
                    user=user,
                    transaction_type=TransactionType.ADJUST,
                    reference_order=order,
                ).aggregate(total=Sum("points"))["total"]
                or 0
            )

            # ADJUST sum should negate EARN sum (or be clamped)
            assert adjust_sum == -earn_sum, (
                f"ADJUST sum ({adjust_sum}) should equal -EARN sum ({-earn_sum})"
            )

            # Net effect should be >= 0
            net = earn_sum + adjust_sum
            assert net >= 0, f"Net effect ({net}) should be >= 0"
        finally:
            _cleanup_loyalty_data()


# ===========================================================================
# Property 14: XP subtracted on reversal, clamped to zero
# Feature: ranking-and-loyalty-points, Property 14: XP subtracted on reversal, clamped to zero
# ===========================================================================


@pytest.mark.django_db
class TestXpSubtractedOnReversal:
    """**Validates: Requirements 6.3**"""

    @given(
        n_items=num_items,
        price=positive_prices,
        quantity=quantities,
        points_factor=points_factors,
        coeff=st.decimals(
            min_value=Decimal("0.01"),
            max_value=Decimal("10.00"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_xp_after_reversal_is_max_zero_xp_minus_reversed(
        self,
        n_items: int,
        price: Decimal,
        quantity: int,
        points_factor: Decimal,
        coeff: Decimal,
    ):
        """After reversal, total_xp = max(0, X - R) where X is XP before
        reversal and R is the reversed points amount.

        **Validates: Requirements 6.3**
        """
        from user.factories.account import UserAccountFactory

        try:
            user = UserAccountFactory()
            order = _create_order(user=user)

            for _ in range(n_items):
                product = _create_product(
                    price=price,
                    points_coefficient=coeff,
                )
                _create_order_item(order, product, quantity)

            mock_settings = _loyalty_settings(
                enabled=True,
                points_factor=float(points_factor),
            )

            # Award points
            with patch(
                "loyalty.services.Setting.get", side_effect=mock_settings
            ):
                LoyaltyService.award_order_points(order.id)

            user.refresh_from_db()
            xp_before_reversal = user.total_xp

            # Reverse the order
            with patch(
                "loyalty.services.Setting.get", side_effect=mock_settings
            ):
                total_reversed = LoyaltyService.reverse_order_points(order.id)

            user.refresh_from_db()
            expected_xp = max(0, xp_before_reversal - total_reversed)

            assert user.total_xp == expected_xp, (
                f"XP before={xp_before_reversal}, reversed={total_reversed}, "
                f"expected={expected_xp}, got={user.total_xp}"
            )
        finally:
            _cleanup_loyalty_data()


# ===========================================================================
# Property 15: Expiration identifies correct transactions
# Feature: ranking-and-loyalty-points, Property 15: Expiration identifies correct transactions
# ===========================================================================


@pytest.mark.django_db
class TestExpirationIdentifiesCorrectTransactions:
    """**Validates: Requirements 7.1, 7.2, 7.4**"""

    @given(
        expiration_days=expiration_days_positive,
        n_old=st.integers(min_value=1, max_value=5),
        n_recent=st.integers(min_value=0, max_value=5),
        points_per_tx=st.integers(min_value=1, max_value=500),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_only_old_earn_transactions_are_expired(
        self,
        expiration_days: int,
        n_old: int,
        n_recent: int,
        points_per_tx: int,
    ):
        """When D > 0, only EARN transactions older than D days shall be
        expired. Recent transactions are untouched.

        **Validates: Requirements 7.1, 7.2**
        """
        from user.factories.account import UserAccountFactory

        try:
            user = UserAccountFactory()

            now = timezone.now()
            old_date = now - timedelta(days=expiration_days + 1)
            recent_date = now - timedelta(days=max(0, expiration_days - 1))

            # Create old EARN transactions (should be expired)
            for i in range(n_old):
                tx = PointsTransaction.objects.create(
                    user=user,
                    points=points_per_tx,
                    transaction_type=TransactionType.EARN,
                    description=f"Old earn {i}",
                )
                # Directly update created_at to bypass auto_now_add
                PointsTransaction.objects.filter(pk=tx.pk).update(
                    created_at=old_date
                )

            # Create recent EARN transactions (should NOT be expired)
            for i in range(n_recent):
                tx = PointsTransaction.objects.create(
                    user=user,
                    points=points_per_tx,
                    transaction_type=TransactionType.EARN,
                    description=f"Recent earn {i}",
                )
                PointsTransaction.objects.filter(pk=tx.pk).update(
                    created_at=recent_date
                )

            with patch(
                "loyalty.services.Setting.get",
                side_effect=_loyalty_settings(
                    enabled=True,
                    expiration_days=expiration_days,
                ),
            ):
                count = LoyaltyService.process_expiration()

            # Exactly n_old EXPIRE transactions should be created
            assert count == n_old, (
                f"Expected {n_old} expirations, got {count} "
                f"(expiration_days={expiration_days}, n_old={n_old}, n_recent={n_recent})"
            )

            expire_txs = PointsTransaction.objects.filter(
                user=user,
                transaction_type=TransactionType.EXPIRE,
            )
            assert expire_txs.count() == n_old

            # Each EXPIRE transaction should negate the original EARN amount
            for expire_tx in expire_txs:
                assert expire_tx.points == -points_per_tx, (
                    f"EXPIRE points should be {-points_per_tx}, got {expire_tx.points}"
                )
        finally:
            _cleanup_loyalty_data()

    @given(
        n_transactions=st.integers(min_value=1, max_value=5),
        points_per_tx=st.integers(min_value=1, max_value=500),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_zero_expiration_days_creates_no_expire_transactions(
        self,
        n_transactions: int,
        points_per_tx: int,
    ):
        """When D = 0, no EXPIRE transactions shall be created regardless
        of transaction age.

        **Validates: Requirements 7.4**
        """
        from user.factories.account import UserAccountFactory

        try:
            user = UserAccountFactory()

            # Create old EARN transactions
            old_date = timezone.now() - timedelta(days=365)
            for i in range(n_transactions):
                tx = PointsTransaction.objects.create(
                    user=user,
                    points=points_per_tx,
                    transaction_type=TransactionType.EARN,
                    description=f"Old earn {i}",
                )
                PointsTransaction.objects.filter(pk=tx.pk).update(
                    created_at=old_date
                )

            with patch(
                "loyalty.services.Setting.get",
                side_effect=_loyalty_settings(
                    enabled=True,
                    expiration_days=0,
                ),
            ):
                count = LoyaltyService.process_expiration()

            assert count == 0, f"Expected 0 expirations with D=0, got {count}"

            expire_count = PointsTransaction.objects.filter(
                user=user,
                transaction_type=TransactionType.EXPIRE,
            ).count()
            assert expire_count == 0
        finally:
            _cleanup_loyalty_data()


# ===========================================================================
# Property 18: New customer bonus logic
# Feature: ranking-and-loyalty-points, Property 18: New customer bonus logic
# ===========================================================================


@pytest.mark.django_db
class TestNewCustomerBonusLogic:
    """**Validates: Requirements 9.1, 9.2, 9.3**"""

    @given(
        bonus_points=bonus_points_values,
        price=positive_prices,
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_first_order_gets_bonus_subsequent_does_not(
        self,
        bonus_points: int,
        price: Decimal,
    ):
        """When LOYALTY_NEW_CUSTOMER_BONUS_ENABLED is true and user has zero
        prior EARN transactions, a BONUS transaction is created. If user has
        prior EARN transactions, no BONUS is created.

        **Validates: Requirements 9.1, 9.2**
        """
        from user.factories.account import UserAccountFactory

        try:
            user = UserAccountFactory()

            # First order — should get bonus
            order1 = _create_order(user=user)
            product1 = _create_product(price=price)
            _create_order_item(order1, product1, quantity=1)

            mock_settings = _loyalty_settings(
                enabled=True,
                new_customer_bonus_enabled=True,
                new_customer_bonus_points=bonus_points,
            )

            # Award points for first order
            with patch(
                "loyalty.services.Setting.get", side_effect=mock_settings
            ):
                LoyaltyService.award_order_points(order1.id)
                bonus_result = LoyaltyService.check_new_customer_bonus(
                    user, order1
                )

            assert bonus_result == bonus_points, (
                f"First order should get {bonus_points} bonus, got {bonus_result}"
            )

            bonus_txs = PointsTransaction.objects.filter(
                user=user,
                transaction_type=TransactionType.BONUS,
            )
            assert bonus_txs.count() == 1
            assert bonus_txs.first().points == bonus_points

            # Second order — should NOT get bonus
            order2 = _create_order(user=user)
            product2 = _create_product(price=price)
            _create_order_item(order2, product2, quantity=1)

            with patch(
                "loyalty.services.Setting.get", side_effect=mock_settings
            ):
                LoyaltyService.award_order_points(order2.id)
                bonus_result2 = LoyaltyService.check_new_customer_bonus(
                    user, order2
                )

            assert bonus_result2 == 0, (
                f"Second order should get 0 bonus, got {bonus_result2}"
            )

            # Still only 1 BONUS transaction total
            bonus_txs_after = PointsTransaction.objects.filter(
                user=user,
                transaction_type=TransactionType.BONUS,
            )
            assert bonus_txs_after.count() == 1
        finally:
            _cleanup_loyalty_data()

    @given(
        bonus_points=bonus_points_values,
        price=positive_prices,
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_bonus_disabled_creates_no_bonus_transaction(
        self,
        bonus_points: int,
        price: Decimal,
    ):
        """When LOYALTY_NEW_CUSTOMER_BONUS_ENABLED is false, no BONUS
        transaction shall be created regardless of order history.

        **Validates: Requirements 9.3**
        """
        from user.factories.account import UserAccountFactory

        try:
            user = UserAccountFactory()
            order = _create_order(user=user)
            product = _create_product(price=price)
            _create_order_item(order, product, quantity=1)

            mock_settings = _loyalty_settings(
                enabled=True,
                new_customer_bonus_enabled=False,
                new_customer_bonus_points=bonus_points,
            )

            with patch(
                "loyalty.services.Setting.get", side_effect=mock_settings
            ):
                LoyaltyService.award_order_points(order.id)
                bonus_result = LoyaltyService.check_new_customer_bonus(
                    user, order
                )

            assert bonus_result == 0, (
                f"Bonus should be 0 when disabled, got {bonus_result}"
            )

            bonus_count = PointsTransaction.objects.filter(
                user=user,
                transaction_type=TransactionType.BONUS,
            ).count()
            assert bonus_count == 0
        finally:
            _cleanup_loyalty_data()
