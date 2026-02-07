import pytest
from django.conf import settings
from django.db import transaction
from djmoney.money import Money

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from order.enum.status import OrderStatus
from order.exceptions import (
    InsufficientStockError,
    InvalidOrderDataError,
    InvalidStatusTransitionError,
    OrderCancellationError,
    OrderNotFoundError,
    OrderServiceError,
    PaymentNotFoundError,
    ProductNotFoundError,
    StockReservationError,
)
from order.factories.order import OrderFactory
from order.services import OrderService
from order.stock import StockManager
from product.factories.product import ProductFactory


@pytest.mark.django_db
class TestCustomExceptionsNotReraisedAsValueError:
    """
    Test that custom exceptions are NOT caught and re-raised as ValueError.
    This ensures that the backward compatibility code has been properly removed
    and that exception types are preserved throughout the system.
    """

    # ========================================================================
    # Stock Manager Exception Tests
    # ========================================================================

    @pytest.mark.parametrize(
        "operation,exception_type,setup_scenario,description",
        [
            # InsufficientStockError scenarios
            (
                "reserve_stock",
                InsufficientStockError,
                {"stock": 5, "quantity": 10},
                "reserve_insufficient_stock",
            ),
            (
                "decrement_stock",
                InsufficientStockError,
                {"stock": 3, "quantity": 5},
                "decrement_insufficient_stock",
            ),
            # ProductNotFoundError scenarios
            (
                "reserve_stock",
                ProductNotFoundError,
                {"product_id": 999999, "quantity": 1},
                "reserve_nonexistent_product",
            ),
            (
                "decrement_stock",
                ProductNotFoundError,
                {"product_id": 999999, "quantity": 1},
                "decrement_nonexistent_product",
            ),
            (
                "increment_stock",
                ProductNotFoundError,
                {"product_id": 999999, "quantity": 1},
                "increment_nonexistent_product",
            ),
            (
                "get_available_stock",
                ProductNotFoundError,
                {"product_id": 999999},
                "get_available_nonexistent_product",
            ),
        ],
        ids=[
            "reserve_insufficient_stock",
            "decrement_insufficient_stock",
            "reserve_nonexistent_product",
            "decrement_nonexistent_product",
            "increment_nonexistent_product",
            "get_available_nonexistent_product",
        ],
    )
    def test_stock_manager_preserves_custom_exception_types(
        self, operation, exception_type, setup_scenario, description
    ):
        """
        Test that StockManager operations preserve custom exception types.

        Verifies that custom exceptions from StockManager are NOT caught
        and re-raised as ValueError.
        """
        # Setup product if needed
        product = None
        if "product_id" not in setup_scenario:
            product = ProductFactory.create(
                price=Money("50.00", settings.DEFAULT_CURRENCY),
                stock=setup_scenario.get("stock", 0),
            )
            product.set_current_language("en")
            product.name = f"Test Product - {description}"
            product.save()
            product_id = product.id
        else:
            product_id = setup_scenario["product_id"]

        quantity = setup_scenario.get("quantity", 1)

        # Execute operation and verify exception type
        with pytest.raises(exception_type) as exc_info:
            if operation == "reserve_stock":
                StockManager.reserve_stock(
                    product_id=product_id,
                    quantity=quantity,
                    session_id="test-session-123",
                    user_id=None,
                )
            elif operation == "decrement_stock":
                StockManager.decrement_stock(
                    product_id=product_id,
                    quantity=quantity,
                    order_id=1,
                    reason="test",
                )
            elif operation == "increment_stock":
                StockManager.increment_stock(
                    product_id=product_id,
                    quantity=quantity,
                    order_id=1,
                    reason="test",
                )
            elif operation == "get_available_stock":
                StockManager.get_available_stock(product_id=product_id)

        # Verify exception is NOT ValueError
        exception = exc_info.value
        assert not isinstance(exception, ValueError), (
            f"Exception should be {exception_type.__name__}, not ValueError"
        )

        # Verify exception is the correct custom type
        assert isinstance(exception, exception_type), (
            f"Exception should be {exception_type.__name__}"
        )

        # Verify exception is also an OrderServiceError (base class)
        assert isinstance(exception, OrderServiceError), (
            "Exception should inherit from OrderServiceError"
        )

    # ========================================================================
    # Order Service Exception Tests
    # ========================================================================

    @pytest.mark.parametrize(
        "operation,exception_type,description",
        [
            # InvalidStatusTransitionError scenarios
            (
                "update_status_invalid",
                InvalidStatusTransitionError,
                "invalid_status_transition",
            ),
            # InsufficientStockError scenarios
            (
                "create_order_insufficient_stock",
                InsufficientStockError,
                "create_order_no_stock",
            ),
        ],
        ids=[
            "invalid_status_transition",
            "create_order_no_stock",
        ],
    )
    def test_order_service_preserves_custom_exception_types(
        self, operation, exception_type, description
    ):
        """
        Test that OrderService operations preserve custom exception types.

        Verifies that custom exceptions from OrderService are NOT caught
        and re-raised as ValueError.
        """
        if operation == "update_status_invalid":
            # Create order in SHIPPED status
            order = OrderFactory.create(status=OrderStatus.SHIPPED)

            # Attempt invalid backwards transition
            with pytest.raises(exception_type) as exc_info:
                OrderService.update_order_status(order, OrderStatus.PROCESSING)

            # Verify exception is NOT ValueError
            exception = exc_info.value
            assert not isinstance(exception, ValueError), (
                f"Exception should be {exception_type.__name__}, not ValueError"
            )
            assert isinstance(exception, exception_type), (
                f"Exception should be {exception_type.__name__}"
            )

        elif operation == "create_order_insufficient_stock":
            # Create product with insufficient stock
            product = ProductFactory.create(
                price=Money("50.00", settings.DEFAULT_CURRENCY), stock=1
            )
            product.set_current_language("en")
            product.name = "Test Product"
            product.save()

            # Prepare order data requesting more than available
            order_data = {
                "shipping_price": Money("10.00", settings.DEFAULT_CURRENCY),
            }
            items_data = [
                {
                    "product": product,
                    "quantity": 10,  # More than available (1)
                }
            ]

            # Attempt to create order
            with pytest.raises(exception_type) as exc_info:
                OrderService.create_order(
                    order_data=order_data, items_data=items_data, user=None
                )

            # Verify exception is NOT ValueError
            exception = exc_info.value
            assert not isinstance(exception, ValueError), (
                f"Exception should be {exception_type.__name__}, not ValueError"
            )
            assert isinstance(exception, exception_type), (
                f"Exception should be {exception_type.__name__}"
            )

    # ========================================================================
    # Order Creation from Cart Exception Tests
    # ========================================================================

    @pytest.mark.parametrize(
        "scenario,exception_type,description",
        [
            # PaymentNotFoundError scenarios
            (
                "missing_payment_intent",
                PaymentNotFoundError,
                "no_payment_intent_id",
            ),
            # InvalidOrderDataError scenarios
            (
                "invalid_address",
                InvalidOrderDataError,
                "missing_required_address_fields",
            ),
        ],
        ids=[
            "no_payment_intent_id",
            "missing_required_address_fields",
        ],
    )
    def test_create_order_from_cart_preserves_custom_exception_types(
        self, scenario, exception_type, description
    ):
        """
        Test that create_order_from_cart preserves custom exception types.

        Verifies that custom exceptions from order creation are NOT caught
        and re-raised as ValueError.
        """
        # Create product and cart
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=10
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        cart = CartFactory.create()
        CartItemFactory.create(cart=cart, product=product, quantity=1)

        if scenario == "missing_payment_intent":
            # Missing payment intent ID
            with pytest.raises(exception_type) as exc_info:
                OrderService.create_order_from_cart(
                    cart=cart,
                    shipping_address={
                        "first_name": "John",
                        "last_name": "Doe",
                        "email": "john@example.com",
                        "street": "Main St",
                        "street_number": "123",
                        "city": "Test City",
                        "zipcode": "12345",
                        "country_id": 1,
                        "phone": "1234567890",
                    },
                    payment_intent_id=None,  # Missing payment intent
                    pay_way=None,
                    user=None,
                )

        elif scenario == "invalid_address":
            # Invalid address (missing required fields)
            with pytest.raises(exception_type) as exc_info:
                OrderService.create_order_from_cart(
                    cart=cart,
                    shipping_address={
                        "first_name": "John",
                        # Missing other required fields
                    },
                    payment_intent_id="pi_test_123",
                    pay_way=None,
                    user=None,
                )

        # Verify exception is NOT ValueError
        exception = exc_info.value
        assert not isinstance(exception, ValueError), (
            f"Exception should be {exception_type.__name__}, not ValueError"
        )
        assert isinstance(exception, exception_type), (
            f"Exception should be {exception_type.__name__}"
        )

    # ========================================================================
    # Order Cancellation Exception Tests
    # ========================================================================

    @pytest.mark.parametrize(
        "order_status,exception_type,description",
        [
            # OrderCancellationError scenarios - orders that cannot be cancelled
            (
                OrderStatus.SHIPPED,
                OrderCancellationError,
                "cannot_cancel_shipped",
            ),
            (
                OrderStatus.DELIVERED,
                OrderCancellationError,
                "cannot_cancel_delivered",
            ),
            (
                OrderStatus.COMPLETED,
                OrderCancellationError,
                "cannot_cancel_completed",
            ),
            (
                OrderStatus.CANCELED,
                OrderCancellationError,
                "cannot_cancel_already_canceled",
            ),
        ],
        ids=[
            "cannot_cancel_shipped",
            "cannot_cancel_delivered",
            "cannot_cancel_completed",
            "cannot_cancel_already_canceled",
        ],
    )
    def test_cancel_order_preserves_custom_exception_types(
        self, order_status, exception_type, description
    ):
        """
        Test that cancel_order preserves custom exception types.

        Verifies that custom exceptions from order cancellation are NOT caught
        and re-raised as ValueError.
        """
        # Create order with specified status
        order = OrderFactory.create(status=order_status)

        # Attempt to cancel order
        with pytest.raises(exception_type) as exc_info:
            OrderService.cancel_order(
                order=order,
                reason="Test cancellation",
                canceled_by="test_user",
                refund_payment=False,
            )

        # Verify exception is NOT ValueError
        exception = exc_info.value
        assert not isinstance(exception, ValueError), (
            f"Exception should be {exception_type.__name__}, not ValueError"
        )
        assert isinstance(exception, exception_type), (
            f"Exception should be {exception_type.__name__}"
        )

    # ========================================================================
    # Comprehensive Exception Type Verification
    # ========================================================================

    @pytest.mark.parametrize(
        "custom_exception_class",
        [
            InsufficientStockError,
            StockReservationError,
            ProductNotFoundError,
            InvalidOrderDataError,
            OrderNotFoundError,
            InvalidStatusTransitionError,
            OrderCancellationError,
            PaymentNotFoundError,
        ],
        ids=[
            "InsufficientStockError",
            "StockReservationError",
            "ProductNotFoundError",
            "InvalidOrderDataError",
            "OrderNotFoundError",
            "InvalidStatusTransitionError",
            "OrderCancellationError",
            "PaymentNotFoundError",
        ],
    )
    def test_custom_exceptions_are_not_subclasses_of_value_error(
        self, custom_exception_class
    ):
        """
        Test that custom exceptions are NOT subclasses of ValueError.

        Verifies that our custom exception hierarchy does not inherit from
        ValueError, ensuring they cannot be accidentally caught as ValueError.
        """
        # Verify custom exception is NOT a subclass of ValueError
        assert not issubclass(custom_exception_class, ValueError), (
            f"{custom_exception_class.__name__} should NOT be a subclass of ValueError"
        )

        # Verify custom exception IS a subclass of OrderServiceError
        assert issubclass(custom_exception_class, OrderServiceError), (
            f"{custom_exception_class.__name__} should be a subclass of OrderServiceError"
        )

        # Verify custom exception IS a subclass of Exception
        assert issubclass(custom_exception_class, Exception), (
            f"{custom_exception_class.__name__} should be a subclass of Exception"
        )

    # ========================================================================
    # Exception Catching Behavior Tests
    # ========================================================================

    def test_custom_exceptions_cannot_be_caught_as_value_error(self):
        """
        Test that custom exceptions cannot be caught as ValueError.

        Verifies that when a custom exception is raised, it cannot be
        caught by an except ValueError clause.
        """
        # Create product with insufficient stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=5
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Track which exception was caught
        caught_as_value_error = False
        caught_as_custom_exception = False

        try:
            # This should raise InsufficientStockError
            StockManager.reserve_stock(
                product_id=product.id,
                quantity=10,
                session_id="test-session",
                user_id=None,
            )
        except ValueError:
            # Should NOT reach here
            caught_as_value_error = True
        except InsufficientStockError:
            # Should reach here
            caught_as_custom_exception = True

        # Verify exception was caught as custom type, not ValueError
        assert not caught_as_value_error, (
            "Custom exception should NOT be caught as ValueError"
        )
        assert caught_as_custom_exception, (
            "Custom exception should be caught as InsufficientStockError"
        )

    def test_order_service_does_not_catch_and_reraise_as_value_error(self):
        """
        Test that OrderService does not catch custom exceptions and re-raise as ValueError.

        This test verifies that the backward compatibility code (task 2.2) has been
        removed and custom exceptions are not caught and re-raised as ValueError.
        """
        # Create order in SHIPPED status
        order = OrderFactory.create(status=OrderStatus.SHIPPED)

        # Track exception type
        exception_caught = None

        try:
            # Attempt invalid backwards transition
            OrderService.update_order_status(order, OrderStatus.PROCESSING)
        except ValueError as e:
            # Should NOT reach here - would indicate backward compatibility code still exists
            exception_caught = ("ValueError", e)
        except InvalidStatusTransitionError as e:
            # Should reach here - correct custom exception
            exception_caught = ("InvalidStatusTransitionError", e)
        except Exception as e:
            # Catch any other exception
            exception_caught = (type(e).__name__, e)

        # Verify exception was caught as custom type
        assert exception_caught is not None, (
            "An exception should have been raised"
        )
        assert exception_caught[0] == "InvalidStatusTransitionError", (
            f"Exception should be InvalidStatusTransitionError, not {exception_caught[0]}"
        )
        assert not isinstance(exception_caught[1], ValueError), (
            "Exception should NOT be an instance of ValueError"
        )

    # ========================================================================
    # Integration Tests with Multiple Operations
    # ========================================================================

    def test_exception_types_preserved_through_transaction_rollback(self):
        """
        Test that exception types are preserved even during transaction rollback.

        Verifies that when a transaction fails and rolls back, the original
        custom exception type is preserved and not converted to ValueError.
        """
        # Create product with insufficient stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=1
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Prepare order data
        order_data = {
            "shipping_price": Money("10.00", settings.DEFAULT_CURRENCY),
        }
        items_data = [
            {
                "product": product,
                "quantity": 10,  # More than available
            }
        ]

        # Track exception type
        exception_caught = None

        try:
            with transaction.atomic():
                OrderService.create_order(
                    order_data=order_data, items_data=items_data, user=None
                )
        except ValueError as e:
            # Should NOT reach here
            exception_caught = ("ValueError", e)
        except InsufficientStockError as e:
            # Should reach here
            exception_caught = ("InsufficientStockError", e)
        except Exception as e:
            # Catch any other exception
            exception_caught = (type(e).__name__, e)

        # Verify exception type preserved through rollback
        assert exception_caught is not None, (
            "An exception should have been raised"
        )
        assert exception_caught[0] == "InsufficientStockError", (
            f"Exception should be InsufficientStockError, not {exception_caught[0]}"
        )
        assert not isinstance(exception_caught[1], ValueError), (
            "Exception should NOT be an instance of ValueError"
        )

    def test_nested_operations_preserve_exception_types(self):
        """
        Test that exception types are preserved through nested operations.

        Verifies that when operations call other operations that raise
        custom exceptions, the exception type is preserved through the call stack.
        """
        # Create product
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=10
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Create order
        order = OrderFactory.create(status=OrderStatus.SHIPPED)

        # Track exception type from nested operation
        exception_caught = None

        try:
            # This calls update_order_status internally, which should raise
            # InvalidStatusTransitionError for invalid transition
            OrderService.update_order_status(order, OrderStatus.PROCESSING)
        except ValueError as e:
            # Should NOT reach here
            exception_caught = ("ValueError", e)
        except InvalidStatusTransitionError as e:
            # Should reach here
            exception_caught = ("InvalidStatusTransitionError", e)
        except Exception as e:
            # Catch any other exception
            exception_caught = (type(e).__name__, e)

        # Verify exception type preserved through nested calls
        assert exception_caught is not None, (
            "An exception should have been raised"
        )
        assert exception_caught[0] == "InvalidStatusTransitionError", (
            f"Exception should be InvalidStatusTransitionError, not {exception_caught[0]}"
        )
        assert not isinstance(exception_caught[1], ValueError), (
            "Exception should NOT be an instance of ValueError"
        )
