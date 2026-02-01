import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from djmoney.money import Money

from order.factories.order import OrderFactory
from order.models.item import OrderItem
from product.factories.product import ProductFactory


@pytest.mark.django_db(transaction=True)
class TestStockValidationPreventsInvalidOrders:
    """
    Test that OrderItem creation with quantity exceeding available stock
    is properly rejected with appropriate error.
    """

    def setup_method(self):
        """Set up test data for each test method."""
        self.order = OrderFactory.create()

    @pytest.mark.parametrize(
        "stock,quantity,should_fail,description",
        [
            # Test case 1: Quantity exceeds stock significantly
            (5, 10, True, "quantity_double_stock"),
            # Test case 2: Zero stock, any quantity should fail
            (0, 1, True, "zero_stock_one_quantity"),
            # Test case 3: Quantity exceeds stock by small amount
            (3, 5, True, "quantity_slightly_over_stock"),
            # Test case 4: Quantity exactly equals stock (should succeed)
            (10, 10, False, "quantity_equals_stock"),
            # Test case 5: Quantity less than stock (should succeed)
            (10, 5, False, "quantity_less_than_stock"),
            # Test case 6: Edge case - one more than stock
            (5, 6, True, "one_over_stock"),
            # Test case 7: Edge case - one less than stock
            (5, 4, False, "one_under_stock"),
            # Test case 8: Large stock, quantity exceeds
            (100, 150, True, "large_stock_exceeded"),
            # Test case 9: Large stock, quantity within limit
            (100, 50, False, "large_stock_within_limit"),
            # Test case 10: Minimum valid case
            (1, 1, False, "minimum_valid_case"),
            # Test case 11: Zero stock, large quantity
            (0, 100, True, "zero_stock_large_quantity"),
        ],
        ids=[
            "quantity_double_stock",
            "zero_stock_one_quantity",
            "quantity_slightly_over_stock",
            "quantity_equals_stock",
            "quantity_less_than_stock",
            "one_over_stock",
            "one_under_stock",
            "large_stock_exceeded",
            "large_stock_within_limit",
            "minimum_valid_case",
            "zero_stock_large_quantity",
        ],
    )
    def test_orderitem_creation_validates_stock(
        self, stock, quantity, should_fail, description
    ):
        """
        Test that OrderItem creation validates quantity against available stock.

        For each stock/quantity combination:
        1. Create product with specified stock
        2. Attempt to create OrderItem with specified quantity
        3. Verify ValidationError raised if quantity > stock
        4. Verify successful creation if quantity <= stock
        """
        # Create product with specified stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=stock
        )
        product.set_current_language("en")
        product.name = f"Test Product - {description}"
        product.save()

        # Create OrderItem (not saved yet)
        order_item = OrderItem(
            order=self.order,
            product=product,
            price=product.price,
            quantity=quantity,
        )

        if should_fail:
            # Verify ValidationError raised when quantity > stock
            with pytest.raises(ValidationError) as exc_info:
                order_item.clean()

            # Verify error message mentions stock
            error_message = str(exc_info.value)
            assert (
                "stock" in error_message.lower()
                or "quantity" in error_message.lower()
            ), (
                f"Error message should mention stock or quantity: {error_message}"
            )

            # Verify product stock unchanged
            product.refresh_from_db()
            assert product.stock == stock, (
                f"Product stock should remain {stock}, got {product.stock}"
            )
        else:
            # Verify no error raised when quantity <= stock
            order_item.clean()  # Should not raise

            # Verify we can save the item
            order_item.save()

            # Verify item was created
            assert order_item.pk is not None, "OrderItem should be saved"
            assert order_item.quantity == quantity, (
                f"OrderItem quantity should be {quantity}, got {order_item.quantity}"
            )

    @pytest.mark.parametrize(
        "stock,quantity",
        [
            (5, 10),
            (0, 1),
            (3, 5),
            (10, 11),
            (1, 2),
        ],
        ids=[
            "stock_5_quantity_10",
            "stock_0_quantity_1",
            "stock_3_quantity_5",
            "stock_10_quantity_11",
            "stock_1_quantity_2",
        ],
    )
    def test_orderitem_clean_prevents_insufficient_stock(self, stock, quantity):
        """
        Test that OrderItem.clean() prevents creation when quantity > stock.

        This test focuses specifically on the clean() method validation
        for insufficient stock scenarios.
        """
        # Create product with specified stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=stock
        )
        product.set_current_language("en")
        product.name = f"Test Product - stock {stock}"
        product.save()

        # Create new OrderItem (not saved)
        order_item = OrderItem(
            order=self.order,
            product=product,
            price=product.price,
            quantity=quantity,
        )

        # Verify ValidationError raised
        with pytest.raises(ValidationError) as exc_info:
            order_item.clean()

        # Verify error message is appropriate
        error_message = str(exc_info.value)
        assert (
            "stock" in error_message.lower()
            or "exceed" in error_message.lower()
        ), f"Error should mention stock or exceed: {error_message}"

        # Verify product stock unchanged
        product.refresh_from_db()
        assert product.stock == stock, (
            f"Product stock should remain unchanged at {stock}"
        )

    @pytest.mark.parametrize(
        "stock,quantity",
        [
            (10, 10),
            (10, 5),
            (5, 1),
            (100, 50),
            (1, 1),
        ],
        ids=[
            "exact_match",
            "half_stock",
            "one_from_five",
            "half_large_stock",
            "minimum_valid",
        ],
    )
    def test_orderitem_creation_succeeds_with_sufficient_stock(
        self, stock, quantity
    ):
        """
        Test that OrderItem creation succeeds when quantity <= stock.

        This test verifies the positive case - that valid orders
        are not incorrectly rejected.
        """
        # Create product with specified stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=stock
        )
        product.set_current_language("en")
        product.name = f"Test Product - stock {stock}"
        product.save()

        # Create OrderItem
        order_item = OrderItem(
            order=self.order,
            product=product,
            price=product.price,
            quantity=quantity,
        )

        # Verify clean() passes
        order_item.clean()  # Should not raise

        # Verify save succeeds
        order_item.save()

        # Verify item was created correctly
        assert order_item.pk is not None, "OrderItem should be saved"
        assert order_item.quantity == quantity, (
            f"OrderItem quantity should be {quantity}"
        )
        assert order_item.product == product, (
            "OrderItem should reference correct product"
        )
        assert order_item.order == self.order, (
            "OrderItem should reference correct order"
        )

    def test_existing_orderitem_stock_not_checked(self):
        """
        Test that existing OrderItems are not validated against current stock.

        Stock validation should only apply to NEW OrderItems (pk is None).
        Existing items should be allowed to update without stock checks.
        """
        # Create product with sufficient stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=10
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Create and save OrderItem
        order_item = OrderItem(
            order=self.order,
            product=product,
            price=product.price,
            quantity=5,
        )
        order_item.clean()
        order_item.save()

        # Reduce stock below OrderItem quantity
        product.stock = 2
        product.save()

        # Verify existing item can still be cleaned/updated
        order_item.quantity = 6  # Still more than current stock (2)
        order_item.clean()  # Should not raise because item already exists

        # Verify we can save the update
        order_item.save()
        assert order_item.quantity == 6

    @pytest.mark.parametrize(
        "initial_stock,first_quantity,second_quantity",
        [
            (10, 5, 6),  # First order takes 5, second wants 6 (only 5 left)
            (
                10,
                5,
                5,
            ),  # First order takes 5, second wants 5 (exactly 5 left - should succeed)
            (
                10,
                5,
                3,
            ),  # First order takes 5, second wants 3 (enough stock remaining)
            (
                20,
                15,
                5,
            ),  # First order takes 15, second wants 5 (exactly 5 left - should succeed)
        ],
        ids=[
            "sequential_orders_exceed_stock",
            "sequential_orders_exact_match",
            "sequential_orders_within_stock",
            "large_orders_exact_match",
        ],
    )
    def test_sequential_orderitems_validate_remaining_stock(
        self, initial_stock, first_quantity, second_quantity
    ):
        """
        Test that stock validation considers previously created OrderItems.

        When multiple OrderItems are created for the same product,
        each should validate against the remaining stock.

        Note: This test validates the clean() method behavior. In production,
        the StockManager handles atomic stock operations. We avoid testing
        scenarios that would result in negative stock as the database has
        a check constraint preventing this.
        """
        # Create product with initial stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=initial_stock
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Create first OrderItem
        first_item = OrderItem(
            order=self.order,
            product=product,
            price=product.price,
            quantity=first_quantity,
        )
        first_item.clean()
        first_item.save()

        # Calculate remaining stock
        remaining_stock = initial_stock - first_quantity

        # Update product stock to reflect the first order
        # (in production, StockManager would handle this atomically)
        product.stock = remaining_stock
        product.save()

        # Attempt to create second OrderItem
        second_order = OrderFactory.create()
        second_item = OrderItem(
            order=second_order,
            product=product,
            price=product.price,
            quantity=second_quantity,
        )

        if second_quantity > remaining_stock:
            # Should fail - not enough stock remaining
            with pytest.raises(ValidationError) as exc_info:
                second_item.clean()

            error_message = str(exc_info.value)
            assert "stock" in error_message.lower(), (
                f"Error should mention stock: {error_message}"
            )
        else:
            # Should succeed - enough stock remaining
            second_item.clean()
            second_item.save()
            assert second_item.pk is not None

    def test_zero_quantity_rejected(self):
        """
        Test that OrderItem with zero quantity is rejected.

        While not strictly a stock validation issue, zero quantity
        should also be rejected as invalid.
        """
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=10
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        order_item = OrderItem(
            order=self.order,
            product=product,
            price=product.price,
            quantity=0,
        )

        with pytest.raises(ValidationError) as exc_info:
            order_item.clean()

        error_message = str(exc_info.value)
        assert (
            "quantity" in error_message.lower()
            and "greater" in error_message.lower()
        ), (
            f"Error should mention quantity must be greater than 0: {error_message}"
        )

    def test_negative_quantity_rejected(self):
        """
        Test that OrderItem with negative quantity is rejected.

        Negative quantities should be rejected as invalid.
        """
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=10
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        order_item = OrderItem(
            order=self.order,
            product=product,
            price=product.price,
            quantity=-5,
        )

        with pytest.raises(ValidationError) as exc_info:
            order_item.clean()

        error_message = str(exc_info.value)
        assert (
            "quantity" in error_message.lower()
            and "greater" in error_message.lower()
        ), (
            f"Error should mention quantity must be greater than 0: {error_message}"
        )
