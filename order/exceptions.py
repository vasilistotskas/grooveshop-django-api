"""
Custom exception classes for the order service.

This module defines a hierarchy of exceptions for handling various error
conditions in the order and stock management system. These exceptions provide
clear, specific error types for different failure scenarios, replacing generic
ValueError usage.

Exception Hierarchy:
    OrderServiceError (base)
    ├── StockError
    │   ├── InsufficientStockError
    │   └── StockReservationError
    ├── ProductNotFoundError
    ├── InvalidOrderDataError
    ├── OrderNotFoundError
    ├── InvalidStatusTransitionError
    ├── OrderCancellationError
    └── PaymentError
        ├── PaymentNotFoundError
        ├── PaymentVerificationError
        └── WebhookVerificationError
"""


class OrderServiceError(Exception):
    """
    Base exception for order service errors.

    All custom exceptions in the order service inherit from this base class,
    allowing for broad exception handling when needed while still maintaining
    specific exception types for detailed error handling.
    """

    pass


class StockError(OrderServiceError):
    """
    Base exception for stock-related errors.

    Raised when operations involving product inventory fail. This includes
    insufficient stock, reservation failures, and other stock management issues.
    """

    pass


class InsufficientStockError(StockError):
    """
    Raised when product stock is insufficient for the requested operation.

    This exception is raised when attempting to create an order, reserve stock,
    or decrement stock when the available quantity is less than the requested
    quantity.

    Attributes:
        product_id (int): The ID of the product with insufficient stock
        available (int): The available stock quantity
        requested (int): The requested stock quantity

    Example:
        >>> raise InsufficientStockError(product_id=123, available=5, requested=10)
        InsufficientStockError: Product 123 has insufficient stock. Available: 5, Requested: 10
    """

    def __init__(self, product_id: int, available: int, requested: int):
        """
        Initialize InsufficientStockError with product and quantity details.

        Args:
            product_id: The ID of the product with insufficient stock
            available: The available stock quantity
            requested: The requested stock quantity
        """
        self.product_id = product_id
        self.available = available
        self.requested = requested
        super().__init__(
            f"Product {product_id} has insufficient stock. "
            f"Available: {available}, Requested: {requested}"
        )


class StockReservationError(StockError):
    """
    Raised when stock reservation fails.

    This exception is raised when attempting to create, release, or convert
    a stock reservation and the operation fails for reasons other than
    insufficient stock (e.g., reservation not found, already consumed).

    Attributes:
        message (str): Detailed error message
        reservation_id (int, optional): The ID of the reservation that failed
    """

    def __init__(self, message: str, reservation_id: int | None = None):
        """
        Initialize StockReservationError with error details.

        Args:
            message: Detailed error message
            reservation_id: Optional ID of the reservation that failed
        """
        self.reservation_id = reservation_id
        super().__init__(message)


class ProductNotFoundError(OrderServiceError):
    """
    Raised when a product doesn't exist.

    This exception is raised when attempting to perform operations on a product
    that cannot be found in the database.

    Attributes:
        product_id (int): The ID of the product that was not found
    """

    def __init__(self, product_id: int):
        """
        Initialize ProductNotFoundError with product ID.

        Args:
            product_id: The ID of the product that was not found
        """
        self.product_id = product_id
        super().__init__(f"Product with ID {product_id} not found")


class InvalidOrderDataError(OrderServiceError):
    """
    Raised when order data validation fails.

    This exception is raised when order creation or modification fails due to
    invalid data, such as missing required fields, invalid addresses, price
    mismatches, or other validation failures.

    Attributes:
        message (str): Detailed error message
        field_errors (dict, optional): Field-specific validation errors
    """

    def __init__(self, message: str, field_errors: dict | None = None):
        """
        Initialize InvalidOrderDataError with validation details.

        Args:
            message: Detailed error message
            field_errors: Optional dictionary of field-specific errors
        """
        self.field_errors = field_errors or {}
        super().__init__(message)


class OrderNotFoundError(OrderServiceError):
    """
    Raised when an order doesn't exist.

    This exception is raised when attempting to perform operations on an order
    that cannot be found in the database.

    Attributes:
        order_id (int | str): The ID or UUID of the order that was not found
    """

    def __init__(self, order_id: int | str):
        """
        Initialize OrderNotFoundError with order ID.

        Args:
            order_id: The ID or UUID of the order that was not found
        """
        self.order_id = order_id
        super().__init__(f"Order with ID {order_id} not found")


class InvalidStatusTransitionError(OrderServiceError):
    """
    Raised when an order status transition is invalid.

    This exception is raised when attempting to change an order's status to
    a state that is not allowed from the current state according to the
    order state machine.

    Attributes:
        current_status (str): The current order status
        new_status (str): The attempted new status
        allowed (list[str]): List of allowed status transitions from current state

    Example:
        >>> raise InvalidStatusTransitionError(
        ...     current_status='SHIPPED',
        ...     new_status='PROCESSING',
        ...     allowed=['DELIVERED', 'RETURNED']
        ... )
        InvalidStatusTransitionError: Cannot transition from SHIPPED to PROCESSING.
        Allowed transitions: DELIVERED, RETURNED
    """

    def __init__(
        self, current_status: str, new_status: str, allowed: list[str]
    ):
        """
        Initialize InvalidStatusTransitionError with transition details.

        Args:
            current_status: The current order status
            new_status: The attempted new status
            allowed: List of allowed status transitions from current state
        """
        self.current_status = current_status
        self.new_status = new_status
        self.allowed = allowed
        super().__init__(
            f"Cannot transition from {current_status} to {new_status}. "
            f"Allowed transitions: {', '.join(allowed)}"
        )


class OrderCancellationError(OrderServiceError):
    """
    Raised when an order cannot be cancelled.

    This exception is raised when attempting to cancel an order that is in a
    state where cancellation is not allowed (e.g., already shipped, delivered,
    or cancelled).

    Attributes:
        order_id (int): The ID of the order that cannot be cancelled
        reason (str): The reason why cancellation is not allowed
    """

    def __init__(self, order_id: int, reason: str):
        """
        Initialize OrderCancellationError with order details.

        Args:
            order_id: The ID of the order that cannot be cancelled
            reason: The reason why cancellation is not allowed
        """
        self.order_id = order_id
        self.reason = reason
        super().__init__(f"Cannot cancel order {order_id}: {reason}")


class PaymentError(OrderServiceError):
    """
    Base exception for payment-related errors.

    Raised when operations involving payment processing fail. This includes
    payment intent creation, verification, webhook processing, and other
    payment-related issues.
    """

    pass


class PaymentNotFoundError(PaymentError):
    """
    Raised when a payment intent doesn't exist.

    This exception is raised when attempting to perform operations on a payment
    intent that cannot be found in the payment gateway or database.

    Attributes:
        payment_id (str): The ID of the payment intent that was not found
    """

    def __init__(self, payment_id: str):
        """
        Initialize PaymentNotFoundError with payment ID.

        Args:
            payment_id: The ID of the payment intent that was not found
        """
        self.payment_id = payment_id
        super().__init__(f"Payment intent with ID {payment_id} not found")


class PaymentVerificationError(PaymentError):
    """
    Raised when payment verification fails.

    This exception is raised when attempting to verify a payment and the
    verification fails due to invalid payment status, mismatched amounts,
    or other verification issues.

    Attributes:
        payment_id (str): The ID of the payment that failed verification
        reason (str): The reason for verification failure
    """

    def __init__(self, payment_id: str, reason: str):
        """
        Initialize PaymentVerificationError with verification details.

        Args:
            payment_id: The ID of the payment that failed verification
            reason: The reason for verification failure
        """
        self.payment_id = payment_id
        self.reason = reason
        super().__init__(
            f"Payment verification failed for {payment_id}: {reason}"
        )


class WebhookVerificationError(PaymentError):
    """
    Raised when webhook signature verification fails.

    This exception is raised when receiving a webhook from a payment provider
    and the signature verification fails, indicating a potential security issue
    or invalid webhook.

    Attributes:
        provider (str): The payment provider (e.g., 'stripe', 'paypal')
        reason (str): The reason for verification failure
    """

    def __init__(self, provider: str, reason: str):
        """
        Initialize WebhookVerificationError with verification details.

        Args:
            provider: The payment provider (e.g., 'stripe', 'paypal')
            reason: The reason for verification failure
        """
        self.provider = provider
        self.reason = reason
        super().__init__(
            f"Webhook verification failed for {provider}: {reason}"
        )
