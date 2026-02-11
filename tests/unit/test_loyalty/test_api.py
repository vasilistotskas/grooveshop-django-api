"""Unit tests for Loyalty API endpoints.

Tests cover:
- Summary response structure (GET /api/v1/loyalty/summary)
- Transactions pagination and filtering (GET /api/v1/loyalty/transactions)
- Redeem validation: insufficient balance, disabled system (POST /api/v1/loyalty/redeem)
- Product points preview (GET /api/v1/loyalty/product/<pk>/points)

Requirements: 10.1, 10.2, 10.3, 10.4
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from djmoney.money import Money

from loyalty.enum import TransactionType
from loyalty.models.tier import LoyaltyTier, LoyaltyTierTranslation
from loyalty.models.transaction import PointsTransaction
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_settings_enabled(key, default=None):
    """Mock Setting.get() with loyalty system enabled and sensible defaults."""
    settings_map = {
        "LOYALTY_ENABLED": True,
        "LOYALTY_POINTS_FACTOR": Decimal("1.0"),
        "LOYALTY_XP_PER_LEVEL": 1000,
        "LOYALTY_PRICE_BASIS": "final_price",
        "LOYALTY_TIER_MULTIPLIER_ENABLED": False,
        "LOYALTY_REDEMPTION_RATIO_EUR": Decimal("100.0"),
        "LOYALTY_REDEMPTION_RATIO_USD": Decimal("100.0"),
    }
    return settings_map.get(key, default)


def _mock_settings_disabled(key, default=None):
    """Mock Setting.get() with loyalty system disabled."""
    settings_map = {
        "LOYALTY_ENABLED": False,
    }
    return settings_map.get(key, default)


def _create_tier(
    required_level: int, multiplier: str = "1.00", name: str = "Bronze"
):
    """Create a LoyaltyTier with an English translation."""
    tier = LoyaltyTier.objects.create(
        required_level=required_level,
        points_multiplier=Decimal(multiplier),
    )
    LoyaltyTierTranslation.objects.create(
        master=tier,
        language_code="en",
        name=name,
        description=f"{name} tier",
    )
    return tier


# ===========================================================================
# 1. Summary endpoint — GET /api/v1/loyalty/summary
# Validates: Requirement 10.1
# ===========================================================================


@pytest.mark.django_db
class TestSummaryEndpoint:
    """Test the loyalty summary endpoint response structure and data."""

    def test_summary_returns_expected_fields(self):
        """Summary response contains all required fields."""
        user = UserAccountFactory(num_addresses=0)
        client = APIClient()
        client.force_authenticate(user=user)

        with (
            patch(
                "loyalty.services.Setting.get",
                side_effect=_mock_settings_enabled,
            ),
            patch(
                "loyalty.views.loyalty.Setting.get",
                side_effect=_mock_settings_enabled,
            ),
        ):
            response = client.get("/api/v1/loyalty/summary")

        assert response.status_code == status.HTTP_200_OK
        data = response.data
        # response.data uses snake_case; camelCase conversion happens at renderer level
        assert "points_balance" in data
        assert "total_xp" in data
        assert "level" in data
        assert "tier" in data
        assert "points_to_next_tier" in data

    def test_summary_returns_correct_balance(self):
        """Summary reflects the user's actual points balance."""
        user = UserAccountFactory(num_addresses=0)
        PointsTransaction.objects.create(
            user=user,
            points=200,
            transaction_type=TransactionType.EARN,
            description="Test earn",
        )
        PointsTransaction.objects.create(
            user=user,
            points=-50,
            transaction_type=TransactionType.REDEEM,
            description="Test redeem",
        )

        client = APIClient()
        client.force_authenticate(user=user)

        with (
            patch(
                "loyalty.services.Setting.get",
                side_effect=_mock_settings_enabled,
            ),
            patch(
                "loyalty.views.loyalty.Setting.get",
                side_effect=_mock_settings_enabled,
            ),
        ):
            response = client.get("/api/v1/loyalty/summary")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["points_balance"] == 150

    def test_summary_with_tier(self):
        """Summary includes tier data when user qualifies for a tier."""
        tier = _create_tier(required_level=1, multiplier="1.50", name="Silver")
        user = UserAccountFactory(num_addresses=0, total_xp=2000)
        user.loyalty_tier = tier
        user.save(update_fields=["loyalty_tier"])

        client = APIClient()
        client.force_authenticate(user=user)

        with (
            patch(
                "loyalty.services.Setting.get",
                side_effect=_mock_settings_enabled,
            ),
            patch(
                "loyalty.views.loyalty.Setting.get",
                side_effect=_mock_settings_enabled,
            ),
        ):
            response = client.get("/api/v1/loyalty/summary")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["tier"] is not None
        assert response.data["level"] == 3  # 1 + floor(2000/1000)

    def test_summary_tier_null_when_no_tier(self):
        """Summary returns null tier when user has no qualifying tier."""
        user = UserAccountFactory(num_addresses=0, total_xp=0)

        client = APIClient()
        client.force_authenticate(user=user)

        with (
            patch(
                "loyalty.services.Setting.get",
                side_effect=_mock_settings_enabled,
            ),
            patch(
                "loyalty.views.loyalty.Setting.get",
                side_effect=_mock_settings_enabled,
            ),
        ):
            response = client.get("/api/v1/loyalty/summary")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["tier"] is None


# ===========================================================================
# 2. Transactions endpoint — GET /api/v1/loyalty/transactions
# Validates: Requirement 10.2
# ===========================================================================


@pytest.mark.django_db
class TestTransactionsEndpoint:
    """Test the transactions endpoint pagination and filtering."""

    def test_transactions_returns_paginated_list(self):
        """Transactions endpoint returns a paginated response."""
        user = UserAccountFactory(num_addresses=0)
        for i in range(3):
            PointsTransaction.objects.create(
                user=user,
                points=10 * (i + 1),
                transaction_type=TransactionType.EARN,
                description=f"Earn #{i + 1}",
            )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get("/api/v1/loyalty/transactions")

        assert response.status_code == status.HTTP_200_OK
        # Paginated response has results key
        assert "results" in response.data
        assert len(response.data["results"]) == 3

    def test_transactions_filtered_by_type(self):
        """Transactions can be filtered by transaction_type."""
        user = UserAccountFactory(num_addresses=0)
        PointsTransaction.objects.create(
            user=user,
            points=100,
            transaction_type=TransactionType.EARN,
            description="Earn",
        )
        PointsTransaction.objects.create(
            user=user,
            points=-30,
            transaction_type=TransactionType.REDEEM,
            description="Redeem",
        )
        PointsTransaction.objects.create(
            user=user,
            points=50,
            transaction_type=TransactionType.BONUS,
            description="Bonus",
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            "/api/v1/loyalty/transactions",
            {"transaction_type": "EARN"},
        )

        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]
        assert len(results) == 1
        assert results[0]["transaction_type"] == "EARN"

    def test_transactions_only_shows_own(self):
        """Users only see their own transactions, not other users'."""
        user = UserAccountFactory(num_addresses=0)
        other_user = UserAccountFactory(num_addresses=0)
        PointsTransaction.objects.create(
            user=user,
            points=100,
            transaction_type=TransactionType.EARN,
            description="My earn",
        )
        PointsTransaction.objects.create(
            user=other_user,
            points=200,
            transaction_type=TransactionType.EARN,
            description="Other earn",
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get("/api/v1/loyalty/transactions")

        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]
        assert len(results) == 1
        assert results[0]["points"] == 100


# ===========================================================================
# 3. Redeem endpoint — POST /api/v1/loyalty/redeem
# Validates: Requirement 10.3
# ===========================================================================


@pytest.mark.django_db
class TestRedeemEndpoint:
    """Test the redeem endpoint validation and success cases."""

    def test_redeem_success(self):
        """Successful redemption returns discount and remaining balance."""
        user = UserAccountFactory(num_addresses=0)
        PointsTransaction.objects.create(
            user=user,
            points=500,
            transaction_type=TransactionType.EARN,
            description="Test earn",
        )

        # Create an order with a known items total (100 EUR > 2 EUR discount)
        order = OrderFactory(user=user, num_order_items=0)
        OrderItemFactory(
            order=order,
            price=Money(Decimal("100.00"), "EUR"),
            quantity=1,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        with patch(
            "loyalty.services.Setting.get", side_effect=_mock_settings_enabled
        ):
            response = client.post(
                "/api/v1/loyalty/redeem",
                {
                    "points_amount": 200,
                    "currency": "EUR",
                    "order_id": order.id,
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert data["points_redeemed"] == 200
        assert data["currency"] == "EUR"
        assert Decimal(str(data["discount_amount"])) == Decimal("2.00")
        assert data["remaining_balance"] == 300

    def test_redeem_insufficient_balance(self):
        """Redemption with insufficient balance returns 400."""
        user = UserAccountFactory(num_addresses=0)
        PointsTransaction.objects.create(
            user=user,
            points=50,
            transaction_type=TransactionType.EARN,
            description="Test earn",
        )

        order = OrderFactory(user=user, num_order_items=0)
        OrderItemFactory(
            order=order,
            price=Money(Decimal("100.00"), "EUR"),
            quantity=1,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        with patch(
            "loyalty.services.Setting.get", side_effect=_mock_settings_enabled
        ):
            response = client.post(
                "/api/v1/loyalty/redeem",
                {
                    "points_amount": 100,
                    "currency": "EUR",
                    "order_id": order.id,
                },
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_redeem_disabled_system(self):
        """Redemption when system is disabled returns 400."""
        user = UserAccountFactory(num_addresses=0)
        PointsTransaction.objects.create(
            user=user,
            points=500,
            transaction_type=TransactionType.EARN,
            description="Test earn",
        )

        order = OrderFactory(user=user, num_order_items=0)
        OrderItemFactory(
            order=order,
            price=Money(Decimal("100.00"), "EUR"),
            quantity=1,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        with patch(
            "loyalty.services.Setting.get", side_effect=_mock_settings_disabled
        ):
            response = client.post(
                "/api/v1/loyalty/redeem",
                {
                    "points_amount": 100,
                    "currency": "EUR",
                    "order_id": order.id,
                },
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_redeem_invalid_currency_rejected_by_serializer(self):
        """Redemption with invalid currency is rejected by serializer validation."""
        user = UserAccountFactory(num_addresses=0)

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            "/api/v1/loyalty/redeem",
            {"points_amount": 100, "currency": "GBP", "order_id": 1},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ===========================================================================
# 4. Product points endpoint — GET /api/v1/loyalty/product/<pk>/points
# Validates: Requirement 10.4
# ===========================================================================


@pytest.mark.django_db
class TestProductPointsEndpoint:
    """Test the product points preview endpoint."""

    def test_product_points_returns_expected_fields(self):
        """Product points response contains productId, potentialPoints, tierMultiplierApplied."""
        user = UserAccountFactory(num_addresses=0)
        product = ProductFactory()

        client = APIClient()
        client.force_authenticate(user=user)

        with (
            patch(
                "loyalty.services.Setting.get",
                side_effect=_mock_settings_enabled,
            ),
            patch(
                "loyalty.views.loyalty.Setting.get",
                side_effect=_mock_settings_enabled,
            ),
        ):
            response = client.get(
                f"/api/v1/loyalty/product/{product.pk}/points"
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert "product_id" in data
        assert "potential_points" in data
        assert "tier_multiplier_applied" in data
        assert data["product_id"] == product.pk

    def test_product_points_no_tier_multiplier(self):
        """Without a tier, tierMultiplierApplied is false."""
        user = UserAccountFactory(num_addresses=0)
        product = ProductFactory()

        client = APIClient()
        client.force_authenticate(user=user)

        with (
            patch(
                "loyalty.services.Setting.get",
                side_effect=_mock_settings_enabled,
            ),
            patch(
                "loyalty.views.loyalty.Setting.get",
                side_effect=_mock_settings_enabled,
            ),
        ):
            response = client.get(
                f"/api/v1/loyalty/product/{product.pk}/points"
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["tier_multiplier_applied"] is False

    def test_product_points_nonexistent_product(self):
        """Requesting points for a nonexistent product returns 404."""
        user = UserAccountFactory(num_addresses=0)

        client = APIClient()
        client.force_authenticate(user=user)

        with (
            patch(
                "loyalty.services.Setting.get",
                side_effect=_mock_settings_enabled,
            ),
            patch(
                "loyalty.views.loyalty.Setting.get",
                side_effect=_mock_settings_enabled,
            ),
        ):
            response = client.get("/api/v1/loyalty/product/999999/points")

        assert response.status_code == status.HTTP_404_NOT_FOUND
