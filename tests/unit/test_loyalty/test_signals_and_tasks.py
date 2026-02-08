"""Unit tests for signal handlers and Celery tasks.

Tests cover: signal handler exception isolation, task idempotency
(duplicate order processing), and task retry behavior.

Requirements: 3.6, 11.4
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_product(price: Decimal = Decimal("50.00")):
    """Create a real Product in the database."""
    from product.models.product import Product

    return Product.objects.create(
        price=Money(price, "EUR"),
        stock=100,
        active=True,
        points_coefficient=Decimal("1.00"),
        points=0,
    )


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
    enabled=True, points_factor=1.0, price_basis="final_price"
):
    """Return a mock side_effect for Setting.get() with loyalty settings."""
    settings_map = {
        "LOYALTY_ENABLED": enabled,
        "LOYALTY_POINTS_FACTOR": points_factor,
        "LOYALTY_PRICE_BASIS": price_basis,
        "LOYALTY_TIER_MULTIPLIER_ENABLED": False,
        "LOYALTY_POINTS_EXPIRATION_DAYS": 0,
        "LOYALTY_NEW_CUSTOMER_BONUS_ENABLED": False,
        "LOYALTY_NEW_CUSTOMER_BONUS_POINTS": 100,
        "LOYALTY_XP_PER_LEVEL": 1000,
    }

    def _get(key, default=None):
        return settings_map.get(key, default)

    return _get


# ===========================================================================
# 1. Signal handler exception isolation
# Validates: Requirement 3.6
# ===========================================================================


@pytest.mark.django_db
class TestSignalHandlerExceptionIsolation:
    """Signal handlers catch exceptions so loyalty errors don't break order flow."""

    def test_completed_handler_calls_delay_on_commit(self):
        """Handler calls process_order_points.delay_on_commit with order id."""
        from order.models.order import Order
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()
        order = _create_order(user=user)

        with patch("loyalty.tasks.process_order_points") as mock_task:
            from loyalty.signals import handle_order_completed_loyalty

            handle_order_completed_loyalty(sender=Order, order=order)

            mock_task.delay_on_commit.assert_called_once_with(order.id)

    def test_canceled_handler_calls_delay_on_commit(self):
        """Handler calls reverse_order_points.delay_on_commit with order id."""
        from order.models.order import Order
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()
        order = _create_order(user=user)

        with patch("loyalty.tasks.reverse_order_points") as mock_task:
            from loyalty.signals import handle_order_canceled_loyalty

            handle_order_canceled_loyalty(sender=Order, order=order)

            mock_task.delay_on_commit.assert_called_once_with(order.id)

    def test_refunded_handler_calls_delay_on_commit(self):
        """Handler calls reverse_order_points.delay_on_commit with order id."""
        from order.models.order import Order
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()
        order = _create_order(user=user)

        with patch("loyalty.tasks.reverse_order_points") as mock_task:
            from loyalty.signals import handle_order_refunded_loyalty

            handle_order_refunded_loyalty(sender=Order, order=order)

            mock_task.delay_on_commit.assert_called_once_with(order.id)

    def test_completed_handler_does_not_raise_on_task_error(self):
        """When delay_on_commit raises, the handler catches it and doesn't propagate."""
        from order.models.order import Order
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()
        order = _create_order(user=user)

        with patch("loyalty.tasks.process_order_points") as mock_task:
            mock_task.delay_on_commit.side_effect = RuntimeError("Celery down")

            from loyalty.signals import handle_order_completed_loyalty

            # Should NOT raise — exception is caught internally
            handle_order_completed_loyalty(sender=Order, order=order)

    def test_canceled_handler_does_not_raise_on_task_error(self):
        """When delay_on_commit raises, the canceled handler catches it."""
        from order.models.order import Order
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()
        order = _create_order(user=user)

        with patch("loyalty.tasks.reverse_order_points") as mock_task:
            mock_task.delay_on_commit.side_effect = RuntimeError("Celery down")

            from loyalty.signals import handle_order_canceled_loyalty

            # Should NOT raise
            handle_order_canceled_loyalty(sender=Order, order=order)

    def test_guest_order_does_not_queue_task(self):
        """Handler skips task queueing for guest orders (user_id is None)."""
        from order.models.order import Order

        order = _create_order(user=None)

        with patch("loyalty.tasks.process_order_points") as mock_task:
            from loyalty.signals import handle_order_completed_loyalty

            handle_order_completed_loyalty(sender=Order, order=order)

            mock_task.delay_on_commit.assert_not_called()


# ===========================================================================
# 2. Task idempotency — duplicate order processing
# Validates: Requirement 11.4
# ===========================================================================


@pytest.mark.django_db
class TestTaskIdempotency:
    """Calling process_order_points twice for the same order is a no-op the second time."""

    def test_duplicate_order_processing_creates_no_extra_transactions(self):
        """Second call to award_order_points for same order creates no new EARN transactions."""
        from loyalty.services import LoyaltyService
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()
        order = _create_order(user=user)
        product = _create_product(price=Decimal("100.00"))
        _create_order_item(order, product, quantity=2)

        mock_settings = _loyalty_settings(enabled=True)

        # First call — should create EARN transactions
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            first_result = LoyaltyService.award_order_points(order.id)

        assert first_result > 0
        first_count = PointsTransaction.objects.filter(
            user=user,
            transaction_type=TransactionType.EARN,
            reference_order=order,
        ).count()
        assert first_count == 1  # 1 order item → 1 EARN transaction

        user.refresh_from_db()
        xp_after_first = user.total_xp

        # Second call — should be a no-op (idempotent)
        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            second_result = LoyaltyService.award_order_points(order.id)

        assert second_result == 0

        # No new transactions created
        second_count = PointsTransaction.objects.filter(
            user=user,
            transaction_type=TransactionType.EARN,
            reference_order=order,
        ).count()
        assert second_count == first_count

        # XP unchanged
        user.refresh_from_db()
        assert user.total_xp == xp_after_first

    def test_task_idempotency_via_celery_task(self):
        """Calling the Celery task directly twice produces no duplicates."""
        from loyalty.tasks import process_order_points
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()
        order = _create_order(user=user)
        product = _create_product(price=Decimal("75.00"))
        _create_order_item(order, product, quantity=1)

        mock_settings = _loyalty_settings(enabled=True)

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            result1 = process_order_points(order.id)

        assert result1["status"] == "success"
        assert result1["points_awarded"] > 0

        with patch("loyalty.services.Setting.get", side_effect=mock_settings):
            result2 = process_order_points(order.id)

        # Second call succeeds but awards 0 points (idempotent)
        assert result2["status"] == "success"
        assert result2["points_awarded"] == 0

        # Still only 1 EARN transaction
        earn_count = PointsTransaction.objects.filter(
            user=user,
            transaction_type=TransactionType.EARN,
            reference_order=order,
        ).count()
        assert earn_count == 1


# ===========================================================================
# 3. Task retry behavior
# Validates: Requirement 11.4
# ===========================================================================


@pytest.mark.django_db
class TestTaskRetryBehavior:
    """When the service raises an exception, the task retries."""

    def test_process_order_points_retries_on_service_error(self):
        """process_order_points calls self.retry() when LoyaltyService raises."""
        from celery.exceptions import Retry

        from loyalty.tasks import process_order_points
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()
        order = _create_order(user=user)

        with (
            patch(
                "loyalty.services.LoyaltyService.award_order_points",
                side_effect=ValueError("DB error"),
            ),
            patch.object(
                process_order_points,
                "retry",
                side_effect=Retry("retry"),
            ) as mock_retry,
        ):
            with pytest.raises(Retry):
                process_order_points(order.id)

            mock_retry.assert_called_once()

    def test_reverse_order_points_retries_on_service_error(self):
        """reverse_order_points calls self.retry() when LoyaltyService raises."""
        from celery.exceptions import Retry

        from loyalty.tasks import reverse_order_points
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory()
        order = _create_order(user=user)

        with (
            patch(
                "loyalty.services.LoyaltyService.reverse_order_points",
                side_effect=ValueError("DB error"),
            ),
            patch.object(
                reverse_order_points,
                "retry",
                side_effect=Retry("retry"),
            ) as mock_retry,
        ):
            with pytest.raises(Retry):
                reverse_order_points(order.id)

            mock_retry.assert_called_once()
