"""
Tests for product stock change signals.

Verifies that StockLog entries are automatically created when product stock
is changed manually (e.g., via admin panel).
"""

import pytest

from order.models.stock_log import StockLog
from product.factories import ProductFactory
from user.factories import UserAccountFactory


@pytest.mark.django_db
class TestStockChangeSignals:
    """Test suite for automatic stock logging via signals."""

    def test_stock_increase_creates_log(self):
        """Test that increasing stock creates an INCREMENT log entry."""
        user = UserAccountFactory()
        product = ProductFactory(stock=10)

        # Simulate admin panel stock change
        product.changed_by = user
        product.stock = 15
        product.save()

        # Verify StockLog was created
        logs = StockLog.objects.filter(product=product)
        assert logs.count() == 1

        log = logs.first()
        assert log.operation_type == StockLog.OPERATION_INCREMENT
        assert log.quantity_delta == 5
        assert log.stock_before == 10
        assert log.stock_after == 15
        assert log.reason == "Manual stock adjustment via admin panel"
        assert log.performed_by == user
        assert log.order is None

    def test_stock_decrease_creates_log(self):
        """Test that decreasing stock creates a DECREMENT log entry."""
        user = UserAccountFactory()
        product = ProductFactory(stock=20)

        # Simulate admin panel stock change
        product.changed_by = user
        product.stock = 15
        product.save()

        # Verify StockLog was created
        logs = StockLog.objects.filter(product=product)
        assert logs.count() == 1

        log = logs.first()
        assert log.operation_type == StockLog.OPERATION_DECREMENT
        assert log.quantity_delta == -5
        assert log.stock_before == 20
        assert log.stock_after == 15
        assert log.reason == "Manual stock adjustment via admin panel"
        assert log.performed_by == user

    def test_no_log_when_stock_unchanged(self):
        """Test that no log is created when stock doesn't change."""
        product = ProductFactory(stock=10)

        # Change other fields but not stock
        product.price = 99.99
        product.save()

        # Verify no StockLog was created
        logs = StockLog.objects.filter(product=product)
        assert logs.count() == 0

    def test_multiple_stock_changes_create_multiple_logs(self):
        """Test that multiple stock changes create separate log entries."""
        user = UserAccountFactory()
        product = ProductFactory(stock=10)

        # First change: increase
        product.changed_by = user
        product.stock = 20
        product.save()

        # Second change: decrease
        product.stock = 15
        product.save()

        # Verify two StockLog entries were created
        logs = StockLog.objects.filter(product=product).order_by("created_at")
        assert logs.count() == 2

        # First log: increment
        assert logs[0].operation_type == StockLog.OPERATION_INCREMENT
        assert logs[0].quantity_delta == 10
        assert logs[0].stock_before == 10
        assert logs[0].stock_after == 20

        # Second log: decrement
        assert logs[1].operation_type == StockLog.OPERATION_DECREMENT
        assert logs[1].quantity_delta == -5
        assert logs[1].stock_before == 20
        assert logs[1].stock_after == 15

    def test_stock_log_without_user(self):
        """Test that stock log is created even without a user (system change)."""
        product = ProductFactory(stock=10)

        # Change stock without setting changed_by
        product.stock = 5
        product.save()

        # Verify StockLog was created with null performed_by
        logs = StockLog.objects.filter(product=product)
        assert logs.count() == 1

        log = logs.first()
        assert log.performed_by is None
        assert log.stock_before == 10
        assert log.stock_after == 5
