import pytest

from order.exceptions import (
    InsufficientStockError,
    InvalidOrderDataError,
    InvalidStatusTransitionError,
    OrderCancellationError,
    OrderNotFoundError,
    OrderServiceError,
    PaymentError,
    PaymentNotFoundError,
    PaymentVerificationError,
    ProductNotFoundError,
    StockError,
    StockReservationError,
    WebhookVerificationError,
)


class TestExceptionHierarchy:
    """Test the exception inheritance hierarchy."""

    def test_order_service_error_is_base_exception(self):
        """OrderServiceError should inherit from Exception."""
        assert issubclass(OrderServiceError, Exception)

    def test_stock_error_inherits_from_order_service_error(self):
        """StockError should inherit from OrderServiceError."""
        assert issubclass(StockError, OrderServiceError)

    def test_insufficient_stock_error_inherits_from_stock_error(self):
        """InsufficientStockError should inherit from StockError."""
        assert issubclass(InsufficientStockError, StockError)

    def test_stock_reservation_error_inherits_from_stock_error(self):
        """StockReservationError should inherit from StockError."""
        assert issubclass(StockReservationError, StockError)

    def test_product_not_found_error_inherits_from_order_service_error(self):
        """ProductNotFoundError should inherit from OrderServiceError."""
        assert issubclass(ProductNotFoundError, OrderServiceError)

    def test_invalid_order_data_error_inherits_from_order_service_error(self):
        """InvalidOrderDataError should inherit from OrderServiceError."""
        assert issubclass(InvalidOrderDataError, OrderServiceError)

    def test_order_not_found_error_inherits_from_order_service_error(self):
        """OrderNotFoundError should inherit from OrderServiceError."""
        assert issubclass(OrderNotFoundError, OrderServiceError)

    def test_invalid_status_transition_error_inherits_from_order_service_error(
        self,
    ):
        """InvalidStatusTransitionError should inherit from OrderServiceError."""
        assert issubclass(InvalidStatusTransitionError, OrderServiceError)

    def test_order_cancellation_error_inherits_from_order_service_error(self):
        """OrderCancellationError should inherit from OrderServiceError."""
        assert issubclass(OrderCancellationError, OrderServiceError)

    def test_payment_error_inherits_from_order_service_error(self):
        """PaymentError should inherit from OrderServiceError."""
        assert issubclass(PaymentError, OrderServiceError)

    def test_payment_not_found_error_inherits_from_payment_error(self):
        """PaymentNotFoundError should inherit from PaymentError."""
        assert issubclass(PaymentNotFoundError, PaymentError)

    def test_payment_verification_error_inherits_from_payment_error(self):
        """PaymentVerificationError should inherit from PaymentError."""
        assert issubclass(PaymentVerificationError, PaymentError)

    def test_webhook_verification_error_inherits_from_payment_error(self):
        """WebhookVerificationError should inherit from PaymentError."""
        assert issubclass(WebhookVerificationError, PaymentError)


class TestInsufficientStockError:
    """Test InsufficientStockError exception."""

    def test_initialization_with_all_parameters(self):
        """Should initialize with product_id, available, and requested."""
        error = InsufficientStockError(
            product_id=123, available=5, requested=10
        )

        assert error.product_id == 123
        assert error.available == 5
        assert error.requested == 10

    def test_error_message_format(self):
        """Should format error message with product details."""
        error = InsufficientStockError(
            product_id=123, available=5, requested=10
        )

        expected_message = (
            "Product 123 has insufficient stock. Available: 5, Requested: 10"
        )
        assert str(error) == expected_message

    def test_can_be_caught_as_stock_error(self):
        """Should be catchable as StockError."""
        with pytest.raises(StockError):
            raise InsufficientStockError(product_id=1, available=0, requested=5)

    def test_can_be_caught_as_order_service_error(self):
        """Should be catchable as OrderServiceError."""
        with pytest.raises(OrderServiceError):
            raise InsufficientStockError(product_id=1, available=0, requested=5)


class TestStockReservationError:
    """Test StockReservationError exception."""

    def test_initialization_with_message_only(self):
        """Should initialize with message only."""
        error = StockReservationError("Reservation already consumed")

        assert str(error) == "Reservation already consumed"
        assert error.reservation_id is None

    def test_initialization_with_message_and_reservation_id(self):
        """Should initialize with message and reservation_id."""
        error = StockReservationError(
            "Reservation not found", reservation_id=456
        )

        assert str(error) == "Reservation not found"
        assert error.reservation_id == 456

    def test_can_be_caught_as_stock_error(self):
        """Should be catchable as StockError."""
        with pytest.raises(StockError):
            raise StockReservationError("Test error")


class TestProductNotFoundError:
    """Test ProductNotFoundError exception."""

    def test_initialization_with_product_id(self):
        """Should initialize with product_id."""
        error = ProductNotFoundError(product_id=789)

        assert error.product_id == 789

    def test_error_message_format(self):
        """Should format error message with product ID."""
        error = ProductNotFoundError(product_id=789)

        expected_message = "Product with ID 789 not found"
        assert str(error) == expected_message

    def test_can_be_caught_as_order_service_error(self):
        """Should be catchable as OrderServiceError."""
        with pytest.raises(OrderServiceError):
            raise ProductNotFoundError(product_id=1)


class TestInvalidOrderDataError:
    """Test InvalidOrderDataError exception."""

    def test_initialization_with_message_only(self):
        """Should initialize with message only."""
        error = InvalidOrderDataError("Invalid shipping address")

        assert str(error) == "Invalid shipping address"
        assert error.field_errors == {}

    def test_initialization_with_field_errors(self):
        """Should initialize with message and field_errors."""
        field_errors = {
            "email": ["Invalid email format"],
            "phone": ["Phone number is required"],
        }
        error = InvalidOrderDataError(
            "Validation failed", field_errors=field_errors
        )

        assert str(error) == "Validation failed"
        assert error.field_errors == field_errors

    def test_field_errors_defaults_to_empty_dict(self):
        """Should default field_errors to empty dict if None."""
        error = InvalidOrderDataError("Test error", field_errors=None)

        assert error.field_errors == {}


class TestOrderNotFoundError:
    """Test OrderNotFoundError exception."""

    def test_initialization_with_integer_id(self):
        """Should initialize with integer order ID."""
        error = OrderNotFoundError(order_id=123)

        assert error.order_id == 123
        assert str(error) == "Order with ID 123 not found"

    def test_initialization_with_uuid_string(self):
        """Should initialize with UUID string."""
        uuid_str = "550e8400-e29b-41d4-a716-446655440000"
        error = OrderNotFoundError(order_id=uuid_str)

        assert error.order_id == uuid_str
        assert str(error) == f"Order with ID {uuid_str} not found"


class TestInvalidStatusTransitionError:
    """Test InvalidStatusTransitionError exception."""

    def test_initialization_with_all_parameters(self):
        """Should initialize with current_status, new_status, and allowed."""
        error = InvalidStatusTransitionError(
            current_status="SHIPPED",
            new_status="PROCESSING",
            allowed=["DELIVERED", "RETURNED"],
        )

        assert error.current_status == "SHIPPED"
        assert error.new_status == "PROCESSING"
        assert error.allowed == ["DELIVERED", "RETURNED"]

    def test_error_message_format(self):
        """Should format error message with transition details."""
        error = InvalidStatusTransitionError(
            current_status="SHIPPED",
            new_status="PROCESSING",
            allowed=["DELIVERED", "RETURNED"],
        )

        expected_message = (
            "Cannot transition from SHIPPED to PROCESSING. "
            "Allowed transitions: DELIVERED, RETURNED"
        )
        assert str(error) == expected_message

    def test_error_message_with_single_allowed_transition(self):
        """Should format error message with single allowed transition."""
        error = InvalidStatusTransitionError(
            current_status="PENDING",
            new_status="SHIPPED",
            allowed=["PROCESSING"],
        )

        expected_message = (
            "Cannot transition from PENDING to SHIPPED. "
            "Allowed transitions: PROCESSING"
        )
        assert str(error) == expected_message

    def test_error_message_with_empty_allowed_list(self):
        """Should format error message with empty allowed list."""
        error = InvalidStatusTransitionError(
            current_status="COMPLETED", new_status="PENDING", allowed=[]
        )

        expected_message = (
            "Cannot transition from COMPLETED to PENDING. Allowed transitions: "
        )
        assert str(error) == expected_message


class TestOrderCancellationError:
    """Test OrderCancellationError exception."""

    def test_initialization_with_order_id_and_reason(self):
        """Should initialize with order_id and reason."""
        error = OrderCancellationError(
            order_id=123, reason="Order already shipped"
        )

        assert error.order_id == 123
        assert error.reason == "Order already shipped"

    def test_error_message_format(self):
        """Should format error message with order ID and reason."""
        error = OrderCancellationError(
            order_id=456, reason="Payment already processed"
        )

        expected_message = "Cannot cancel order 456: Payment already processed"
        assert str(error) == expected_message


class TestPaymentNotFoundError:
    """Test PaymentNotFoundError exception."""

    def test_initialization_with_payment_id(self):
        """Should initialize with payment_id."""
        error = PaymentNotFoundError(payment_id="pi_123456789")

        assert error.payment_id == "pi_123456789"

    def test_error_message_format(self):
        """Should format error message with payment ID."""
        error = PaymentNotFoundError(payment_id="pi_123456789")

        expected_message = "Payment intent with ID pi_123456789 not found"
        assert str(error) == expected_message

    def test_can_be_caught_as_payment_error(self):
        """Should be catchable as PaymentError."""
        with pytest.raises(PaymentError):
            raise PaymentNotFoundError(payment_id="pi_test")


class TestPaymentVerificationError:
    """Test PaymentVerificationError exception."""

    def test_initialization_with_payment_id_and_reason(self):
        """Should initialize with payment_id and reason."""
        error = PaymentVerificationError(
            payment_id="pi_123456789", reason="Amount mismatch"
        )

        assert error.payment_id == "pi_123456789"
        assert error.reason == "Amount mismatch"

    def test_error_message_format(self):
        """Should format error message with payment ID and reason."""
        error = PaymentVerificationError(
            payment_id="pi_123456789", reason="Invalid payment status"
        )

        expected_message = "Payment verification failed for pi_123456789: Invalid payment status"
        assert str(error) == expected_message

    def test_can_be_caught_as_payment_error(self):
        """Should be catchable as PaymentError."""
        with pytest.raises(PaymentError):
            raise PaymentVerificationError(payment_id="pi_test", reason="Test")


class TestWebhookVerificationError:
    """Test WebhookVerificationError exception."""

    def test_initialization_with_provider_and_reason(self):
        """Should initialize with provider and reason."""
        error = WebhookVerificationError(
            provider="stripe", reason="Invalid signature"
        )

        assert error.provider == "stripe"
        assert error.reason == "Invalid signature"

    def test_error_message_format(self):
        """Should format error message with provider and reason."""
        error = WebhookVerificationError(
            provider="paypal", reason="Signature mismatch"
        )

        expected_message = (
            "Webhook verification failed for paypal: Signature mismatch"
        )
        assert str(error) == expected_message

    def test_can_be_caught_as_payment_error(self):
        """Should be catchable as PaymentError."""
        with pytest.raises(PaymentError):
            raise WebhookVerificationError(provider="stripe", reason="Test")


class TestExceptionCatching:
    """Test that exceptions can be caught at different levels of the hierarchy."""

    def test_catch_specific_exception(self):
        """Should be able to catch specific exception type."""
        with pytest.raises(InsufficientStockError) as exc_info:
            raise InsufficientStockError(product_id=1, available=0, requested=5)

        assert exc_info.value.product_id == 1

    def test_catch_intermediate_exception(self):
        """Should be able to catch intermediate exception type."""
        with pytest.raises(StockError):
            raise InsufficientStockError(product_id=1, available=0, requested=5)

    def test_catch_base_exception(self):
        """Should be able to catch base OrderServiceError."""
        with pytest.raises(OrderServiceError):
            raise InsufficientStockError(product_id=1, available=0, requested=5)

    def test_catch_multiple_exception_types(self):
        """Should be able to catch multiple exception types."""
        with pytest.raises((InsufficientStockError, ProductNotFoundError)):
            raise ProductNotFoundError(product_id=1)

    def test_exception_context_preserved_when_caught(self):
        """Should preserve exception context when caught."""
        try:
            raise InvalidStatusTransitionError(
                current_status="SHIPPED",
                new_status="PENDING",
                allowed=["DELIVERED"],
            )
        except InvalidStatusTransitionError as e:
            assert e.current_status == "SHIPPED"
            assert e.new_status == "PENDING"
            assert e.allowed == ["DELIVERED"]
