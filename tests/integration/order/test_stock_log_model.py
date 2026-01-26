import pytest
from django.core.exceptions import ValidationError

from order.models import StockLog
from product.factories import ProductFactory
from user.factories import UserAccountFactory


@pytest.mark.django_db
class TestStockLogModel:
    """Test suite for StockLog model."""

    def test_create_stock_log_with_all_fields(self):
        """Test creating a StockLog with all fields populated."""
        product = ProductFactory(stock=100)
        user = UserAccountFactory()

        stock_log = StockLog.objects.create(
            product=product,
            operation_type=StockLog.OPERATION_DECREMENT,
            quantity_delta=-5,
            stock_before=100,
            stock_after=95,
            reason="Order created",
            performed_by=user,
        )

        assert stock_log.id is not None
        assert stock_log.product == product
        assert stock_log.operation_type == StockLog.OPERATION_DECREMENT
        assert stock_log.quantity_delta == -5
        assert stock_log.stock_before == 100
        assert stock_log.stock_after == 95
        assert stock_log.reason == "Order created"
        assert stock_log.performed_by == user
        assert stock_log.created_at is not None
        assert stock_log.updated_at is not None

    def test_create_stock_log_without_optional_fields(self):
        """Test creating a StockLog without optional fields (order, performed_by)."""
        product = ProductFactory(stock=50)

        stock_log = StockLog.objects.create(
            product=product,
            operation_type=StockLog.OPERATION_RESERVE,
            quantity_delta=-10,
            stock_before=50,
            stock_after=40,
            reason="Stock reserved for checkout",
        )

        assert stock_log.id is not None
        assert stock_log.order is None
        assert stock_log.performed_by is None

    def test_stock_log_str_representation(self):
        """Test the string representation of StockLog."""
        product = ProductFactory(stock=100, name="Test Product")

        stock_log = StockLog.objects.create(
            product=product,
            operation_type=StockLog.OPERATION_INCREMENT,
            quantity_delta=10,
            stock_before=90,
            stock_after=100,
            reason="Order cancelled",
        )

        str_repr = str(stock_log)
        assert "StockLog" in str_repr
        assert "INCREMENT" in str_repr
        assert "90 → 100" in str_repr

    def test_is_increase_property(self):
        """Test the is_increase property."""
        product = ProductFactory(stock=100)

        # Test increase
        increase_log = StockLog.objects.create(
            product=product,
            operation_type=StockLog.OPERATION_INCREMENT,
            quantity_delta=10,
            stock_before=90,
            stock_after=100,
            reason="Stock added",
        )
        assert increase_log.is_increase is True
        assert increase_log.is_decrease is False

        # Test decrease
        decrease_log = StockLog.objects.create(
            product=product,
            operation_type=StockLog.OPERATION_DECREMENT,
            quantity_delta=-5,
            stock_before=100,
            stock_after=95,
            reason="Stock removed",
        )
        assert decrease_log.is_increase is False
        assert decrease_log.is_decrease is True

    def test_stock_log_validation_correct_calculation(self):
        """Test that validation passes when stock_after = stock_before + quantity_delta."""
        product = ProductFactory(stock=100)

        stock_log = StockLog(
            product=product,
            operation_type=StockLog.OPERATION_DECREMENT,
            quantity_delta=-10,
            stock_before=100,
            stock_after=90,  # Correct: 100 + (-10) = 90
            reason="Test",
        )

        # Should not raise ValidationError
        stock_log.clean()
        stock_log.save()
        assert stock_log.id is not None

    def test_stock_log_validation_incorrect_calculation(self):
        """Test that validation fails when stock_after != stock_before + quantity_delta."""
        product = ProductFactory(stock=100)

        stock_log = StockLog(
            product=product,
            operation_type=StockLog.OPERATION_DECREMENT,
            quantity_delta=-10,
            stock_before=100,
            stock_after=95,  # Incorrect: 100 + (-10) should be 90, not 95
            reason="Test",
        )

        with pytest.raises(ValidationError) as exc_info:
            stock_log.clean()

        assert "Stock calculation error" in str(exc_info.value)

    def test_stock_log_ordering(self):
        """Test that stock logs are ordered by created_at descending."""
        product = ProductFactory(stock=100)

        # Create logs with slight time differences
        log1 = StockLog.objects.create(
            product=product,
            operation_type=StockLog.OPERATION_DECREMENT,
            quantity_delta=-5,
            stock_before=100,
            stock_after=95,
            reason="First",
        )

        log2 = StockLog.objects.create(
            product=product,
            operation_type=StockLog.OPERATION_DECREMENT,
            quantity_delta=-5,
            stock_before=95,
            stock_after=90,
            reason="Second",
        )

        logs = list(StockLog.objects.all())
        assert logs[0] == log2  # Most recent first
        assert logs[1] == log1

    def test_stock_log_operation_type_choices(self):
        """Test that all operation type choices are valid."""
        product = ProductFactory(stock=100)

        operation_types = [
            StockLog.OPERATION_RESERVE,
            StockLog.OPERATION_RELEASE,
            StockLog.OPERATION_DECREMENT,
            StockLog.OPERATION_INCREMENT,
        ]

        for op_type in operation_types:
            stock_log = StockLog.objects.create(
                product=product,
                operation_type=op_type,
                quantity_delta=0,
                stock_before=100,
                stock_after=100,
                reason=f"Test {op_type}",
            )
            assert stock_log.operation_type == op_type

    def test_stock_log_product_relationship(self):
        """Test that stock log has proper relationship with product."""
        product = ProductFactory(stock=100)

        stock_log = StockLog.objects.create(
            product=product,
            operation_type=StockLog.OPERATION_DECREMENT,
            quantity_delta=-5,
            stock_before=100,
            stock_after=95,
            reason="Test",
        )

        # Verify relationship
        assert stock_log.product == product
        assert stock_log in product.stock_logs.all()

        # Verify CASCADE is configured (even if soft delete prevents it from working)
        from django.db import models

        field = StockLog._meta.get_field("product")
        assert field.remote_field.on_delete == models.CASCADE

    def test_stock_log_set_null_on_user_delete(self):
        """Test that deleting a user sets performed_by to NULL."""
        product = ProductFactory(stock=100)
        user = UserAccountFactory()

        stock_log = StockLog.objects.create(
            product=product,
            operation_type=StockLog.OPERATION_DECREMENT,
            quantity_delta=-5,
            stock_before=100,
            stock_after=95,
            reason="Test",
            performed_by=user,
        )

        user.delete()
        stock_log.refresh_from_db()

        # performed_by should be NULL due to SET_NULL
        assert stock_log.performed_by is None

    @pytest.mark.parametrize(
        "operation_type,quantity_delta,stock_before,stock_after",
        [
            (StockLog.OPERATION_RESERVE, -10, 100, 90),
            (StockLog.OPERATION_RELEASE, 10, 90, 100),
            (StockLog.OPERATION_DECREMENT, -5, 100, 95),
            (StockLog.OPERATION_INCREMENT, 15, 85, 100),
        ],
    )
    def test_stock_log_various_operations(
        self, operation_type, quantity_delta, stock_before, stock_after
    ):
        """Test creating stock logs with various operation types."""
        product = ProductFactory(stock=stock_before)

        stock_log = StockLog.objects.create(
            product=product,
            operation_type=operation_type,
            quantity_delta=quantity_delta,
            stock_before=stock_before,
            stock_after=stock_after,
            reason=f"Test {operation_type}",
        )

        assert stock_log.operation_type == operation_type
        assert stock_log.quantity_delta == quantity_delta
        assert stock_log.stock_before == stock_before
        assert stock_log.stock_after == stock_after
