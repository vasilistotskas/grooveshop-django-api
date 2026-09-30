"""The order exception hierarchy and the context each error carries.

Views and tasks catch these by base class (``PaymentError``,
``StockError``, ``InvalidOrderDataError``, ``OrderServiceError``), so a
class moving in the tree silently changes which handler answers.
"""

import pytest

from order.exceptions import (
    CartNotReadyError,
    InsufficientStockError,
    InvalidOrderDataError,
    InvalidStatusTransitionError,
    OrderCancellationError,
    OrderChangedDuringPaymentError,
    OrderNotFoundError,
    OrderServiceError,
    PaymentAmountMismatchError,
    PaymentCurrencyMismatchError,
    PaymentError,
    PaymentNotFoundError,
    PaymentVerificationError,
    ProductNotFoundError,
    StockError,
    StockReservationError,
    WebhookVerificationError,
)


@pytest.mark.parametrize(
    "exception_class,parent",
    [
        (StockError, OrderServiceError),
        (InsufficientStockError, StockError),
        (StockReservationError, StockError),
        (ProductNotFoundError, OrderServiceError),
        (InvalidOrderDataError, OrderServiceError),
        (CartNotReadyError, InvalidOrderDataError),
        (OrderNotFoundError, OrderServiceError),
        (InvalidStatusTransitionError, OrderServiceError),
        (OrderCancellationError, OrderServiceError),
        (PaymentError, OrderServiceError),
        (PaymentNotFoundError, PaymentError),
        (OrderChangedDuringPaymentError, PaymentError),
        (PaymentVerificationError, PaymentError),
        (PaymentAmountMismatchError, PaymentError),
        (PaymentCurrencyMismatchError, PaymentError),
        (WebhookVerificationError, PaymentError),
    ],
)
def test_exception_hierarchy(exception_class, parent):
    assert exception_class.__mro__[1] is parent
    # Never a ValueError: a broad ``except ValueError`` must not swallow
    # a domain failure.
    assert not issubclass(exception_class, ValueError)


@pytest.mark.parametrize(
    "error,attributes,message",
    [
        (
            InsufficientStockError(product_id=123, available=5, requested=10),
            {"product_id": 123, "available": 5, "requested": 10},
            "Product 123 has insufficient stock. Available: 5, Requested: 10",
        ),
        (
            StockReservationError("Reservation not found", reservation_id=4),
            {"reservation_id": 4},
            "Reservation not found",
        ),
        (
            StockReservationError("Reservation already consumed"),
            {"reservation_id": None},
            "Reservation already consumed",
        ),
        (
            ProductNotFoundError(product_id=456),
            {"product_id": 456},
            "Product with ID 456 not found",
        ),
        (
            InvalidOrderDataError("Validation failed", field_errors=None),
            {"field_errors": {}},
            "Validation failed",
        ),
        (
            InvalidOrderDataError(
                "Validation failed", field_errors={"email": ["Invalid"]}
            ),
            {"field_errors": {"email": ["Invalid"]}},
            "Validation failed",
        ),
        (
            OrderNotFoundError(order_id="550e8400"),
            {"order_id": "550e8400"},
            "Order with ID 550e8400 not found",
        ),
        (
            InvalidStatusTransitionError(
                current_status="SHIPPED",
                new_status="PROCESSING",
                allowed=["DELIVERED", "RETURNED"],
            ),
            {
                "current_status": "SHIPPED",
                "new_status": "PROCESSING",
                "allowed": ["DELIVERED", "RETURNED"],
            },
            (
                "Cannot transition from SHIPPED to PROCESSING. "
                "Allowed transitions: DELIVERED, RETURNED"
            ),
        ),
        (
            OrderCancellationError(order_id=789, reason="already shipped"),
            {"order_id": 789, "reason": "already shipped"},
            "Cannot cancel order 789: already shipped",
        ),
        (
            PaymentNotFoundError(payment_id="pi_1"),
            {"payment_id": "pi_1"},
            "Payment intent with ID pi_1 not found",
        ),
        (
            OrderChangedDuringPaymentError(order_id=42),
            {"order_id": 42},
            "Order 42 changed while a payment was being opened",
        ),
        (
            PaymentVerificationError(payment_id="pi_1", reason="declined"),
            {"payment_id": "pi_1", "reason": "declined"},
            "Payment verification failed for pi_1: declined",
        ),
        (
            WebhookVerificationError(provider="stripe", reason="bad sig"),
            {"provider": "stripe", "reason": "bad sig"},
            "Webhook verification failed for stripe: bad sig",
        ),
    ],
    ids=lambda value: (
        type(value).__name__ if isinstance(value, Exception) else ""
    ),
)
def test_exception_carries_its_context(error, attributes, message):
    for name, value in attributes.items():
        assert getattr(error, name) == value
    assert str(error) == message


def test_cart_not_ready_error_from_validation_result():
    error = CartNotReadyError.from_validation(
        {"errors": ["Cart is empty"], "insufficient_stock": False}
    )

    assert error.errors == ["Cart is empty"]
    assert error.insufficient_stock is False
    assert str(error) == "Cart validation failed: Cart is empty"
