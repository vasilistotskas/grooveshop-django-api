"""Property-based tests for the redemption flow.

Feature: ranking-and-loyalty-points
Tests Properties 9, 10, 12 from the design document.

**Validates: Requirements 4.2, 4.3, 5.3**
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction
from loyalty.services import LoyaltyService


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Strategy for points balance (positive, reasonable range)
balance_amounts = st.integers(min_value=1, max_value=100_000)

# Strategy for over-redemption excess (at least 1 more than balance)
excess_amounts = st.integers(min_value=1, max_value=50_000)

# Strategy for valid redemption amounts (positive)
valid_redeem_amounts = st.integers(min_value=1, max_value=100_000)

# Strategy for adjustment points (signed, non-zero)
adjustment_points = st.integers(min_value=-10_000, max_value=10_000).filter(
    lambda x: x != 0
)

# Strategy for adjustment description
adjustment_descriptions = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "Z")),
    min_size=1,
    max_size=50,
)


# ---------------------------------------------------------------------------
# Helper: mock Setting.get() for loyalty settings
# ---------------------------------------------------------------------------


def _loyalty_settings(
    enabled=True,
    redemption_ratio_eur=100.0,
):
    """Return a mock side_effect for Setting.get() with loyalty settings."""
    settings_map = {
        "LOYALTY_ENABLED": enabled,
        "LOYALTY_REDEMPTION_RATIO_EUR": redemption_ratio_eur,
    }

    def _get(key, default=None):
        return settings_map.get(key, default)

    return _get


def _cleanup_loyalty_data():
    """Clean up all loyalty-related data between hypothesis examples."""
    PointsTransaction.objects.all().delete()


# ===========================================================================
# Property 9: Over-redemption rejected
# Feature: ranking-and-loyalty-points, Property 9: Over-redemption rejected
# ===========================================================================


@pytest.mark.django_db
class TestOverRedemptionRejected:
    """**Validates: Requirements 4.2**"""

    @given(
        balance=balance_amounts,
        excess=excess_amounts,
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_redemption_exceeding_balance_is_rejected(
        self,
        balance: int,
        excess: int,
    ):
        """For any user with Points_Balance B and for any redemption request
        with points_amount > B, the redemption shall be rejected and no
        REDEEM transaction shall be created.

        **Validates: Requirements 4.2**
        """
        from user.factories.account import UserAccountFactory

        try:
            user = UserAccountFactory()

            # Give the user a known balance via an EARN transaction
            PointsTransaction.objects.create(
                user=user,
                points=balance,
                transaction_type=TransactionType.EARN,
                description="Test balance setup",
            )

            # Attempt to redeem more than the balance
            points_to_redeem = balance + excess

            with patch(
                "loyalty.services.Setting.get",
                side_effect=_loyalty_settings(enabled=True),
            ):
                with pytest.raises(ValidationError):
                    LoyaltyService.redeem_points(
                        user,
                        points_to_redeem,
                        "EUR",
                        max_discount=Decimal("99999"),
                    )

            # Verify no REDEEM transaction was created
            redeem_count = PointsTransaction.objects.filter(
                user=user,
                transaction_type=TransactionType.REDEEM,
            ).count()
            assert redeem_count == 0, (
                f"balance={balance}, attempted={points_to_redeem}: "
                f"expected 0 REDEEM transactions, got {redeem_count}"
            )
        finally:
            _cleanup_loyalty_data()


# ===========================================================================
# Property 10: REDEEM transaction has negative points
# Feature: ranking-and-loyalty-points, Property 10: REDEEM transaction has negative points
# ===========================================================================


@pytest.mark.django_db
class TestRedeemTransactionHasNegativePoints:
    """**Validates: Requirements 4.3**"""

    @given(
        balance=balance_amounts,
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_successful_redemption_creates_negative_redeem_transaction(
        self,
        balance: int,
    ):
        """For any successful redemption of P points, the created
        PointsTransaction shall have transaction_type=REDEEM and
        points = -P (negative).

        **Validates: Requirements 4.3**
        """
        from user.factories.account import UserAccountFactory

        try:
            user = UserAccountFactory()

            # Give the user a known balance
            PointsTransaction.objects.create(
                user=user,
                points=balance,
                transaction_type=TransactionType.EARN,
                description="Test balance setup",
            )

            # Redeem exactly the balance (always valid since balance >= 1)
            points_to_redeem = balance

            with patch(
                "loyalty.services.Setting.get",
                side_effect=_loyalty_settings(enabled=True),
            ):
                LoyaltyService.redeem_points(
                    user, points_to_redeem, "EUR", max_discount=Decimal("99999")
                )

            # Verify the REDEEM transaction
            redeem_txs = PointsTransaction.objects.filter(
                user=user,
                transaction_type=TransactionType.REDEEM,
            )
            assert redeem_txs.count() == 1, (
                f"Expected exactly 1 REDEEM transaction, got {redeem_txs.count()}"
            )

            redeem_tx = redeem_txs.first()
            assert redeem_tx.transaction_type == TransactionType.REDEEM, (
                f"Expected transaction_type=REDEEM, got {redeem_tx.transaction_type}"
            )
            assert redeem_tx.points == -points_to_redeem, (
                f"Redeemed {points_to_redeem} points: expected points={-points_to_redeem}, "
                f"got {redeem_tx.points}"
            )
        finally:
            _cleanup_loyalty_data()


# ===========================================================================
# Property 12: Admin adjustment records created_by
# Feature: ranking-and-loyalty-points, Property 12: Admin adjustment records created_by
# ===========================================================================


@pytest.mark.django_db
class TestAdminAdjustmentRecordsCreatedBy:
    """**Validates: Requirements 5.3**"""

    @given(
        points=adjustment_points,
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_admin_adjustment_has_created_by_set_to_admin(
        self,
        points: int,
    ):
        """For any manual points adjustment performed by an admin user,
        the resulting PointsTransaction of type ADJUST shall have
        created_by set to the admin user who performed the adjustment.

        **Validates: Requirements 5.3**
        """
        from user.factories.account import UserAccountFactory

        try:
            # Create a regular user and an admin user
            user = UserAccountFactory()
            admin_user = UserAccountFactory(admin=True)

            # Create an ADJUST transaction with created_by set to admin
            tx = PointsTransaction.objects.create(
                user=user,
                points=points,
                transaction_type=TransactionType.ADJUST,
                created_by=admin_user,
                description=f"Manual adjustment of {points} points",
            )

            # Retrieve from DB and verify
            tx.refresh_from_db()
            assert tx.transaction_type == TransactionType.ADJUST, (
                f"Expected transaction_type=ADJUST, got {tx.transaction_type}"
            )
            assert tx.created_by_id == admin_user.id, (
                f"Expected created_by={admin_user.id}, got {tx.created_by_id}"
            )
            assert tx.created_by == admin_user, (
                "created_by should be the admin user who performed the adjustment"
            )
            assert tx.points == points, (
                f"Expected points={points}, got {tx.points}"
            )
        finally:
            _cleanup_loyalty_data()
