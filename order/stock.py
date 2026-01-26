from datetime import timedelta
from typing import Optional

from django.db import transaction
from django.utils import timezone

from order.exceptions import (
    InsufficientStockError,
    ProductNotFoundError,
    StockReservationError,
)
from order.models import StockLog, StockReservation
from product.models import Product


class StockManager:
    """
    Manages product stock with atomic operations and reservations.

    This class provides methods for:
    - Reserving stock during checkout (with TTL)
    - Releasing reservations (on abandonment or expiration)
    - Converting reservations to permanent decrements (on payment success)
    - Direct stock decrements/increments (for admin operations)
    - Calculating available stock (excluding active reservations)
    - Cleaning up expired reservations

    All operations use database-level locking (SELECT FOR UPDATE) to prevent
    race conditions and ensure atomicity.
    """

    # Default reservation TTL in minutes
    RESERVATION_TTL_MINUTES = 15

    @classmethod
    @transaction.atomic
    def reserve_stock(
        cls,
        product_id: int,
        quantity: int,
        session_id: str,
        user_id: Optional[int] = None,
    ) -> StockReservation:
        """
        Reserve stock for checkout process.

        Creates a temporary stock reservation with a 15-minute TTL. Uses
        SELECT FOR UPDATE to prevent race conditions when multiple customers
        attempt to reserve the same product simultaneously.

        The available stock is calculated as:
            total_stock - sum(active_reservations.quantity)

        Where active reservations are those that are:
        - Not consumed (consumed=False)
        - Not expired (expires_at > now)

        Args:
            product_id: ID of the product to reserve
            quantity: Number of units to reserve
            session_id: Cart UUID or session identifier for tracking
            user_id: Optional user ID (None for guest users)

        Returns:
            StockReservation: The created reservation object

        Raises:
            ProductNotFoundError: If product doesn't exist
            InsufficientStockError: If not enough stock available
            ValueError: If quantity is not positive

        Example:
            >>> reservation = StockManager.reserve_stock(
            ...     product_id=123,
            ...     quantity=2,
            ...     session_id="cart-uuid-123",
            ...     user_id=456
            ... )
            >>> print(reservation.expires_at)
            2024-01-15 10:30:00+00:00  # 15 minutes from now
        """
        # Validate quantity
        if quantity <= 0:
            raise ValueError("Quantity must be positive")

        # Lock the product row to prevent concurrent modifications
        # This ensures atomicity and prevents race conditions
        try:
            product = Product.objects.select_for_update().get(id=product_id)
        except Product.DoesNotExist:
            raise ProductNotFoundError(product_id=product_id)

        # Calculate available stock by excluding active reservations
        # Active = not consumed AND not expired
        now = timezone.now()
        active_reservations = StockReservation.objects.filter(
            product=product, consumed=False, expires_at__gt=now
        )

        # Sum up all active reservation quantities
        reserved_quantity = sum(
            reservation.quantity for reservation in active_reservations
        )

        # Calculate available stock
        available_stock = product.stock - reserved_quantity

        # Validate sufficient stock
        if available_stock < quantity:
            raise InsufficientStockError(
                product_id=product_id,
                available=available_stock,
                requested=quantity,
            )

        # Calculate expiration time (15 minutes from now)
        expires_at = now + timedelta(minutes=cls.RESERVATION_TTL_MINUTES)

        # Create the reservation
        reservation = StockReservation.objects.create(
            product=product,
            quantity=quantity,
            reserved_by_id=user_id,
            session_id=session_id,
            expires_at=expires_at,
            consumed=False,
        )

        # Log the reservation operation for audit trail
        StockLog.objects.create(
            product=product,
            order=None,  # No order yet during reservation
            operation_type=StockLog.OPERATION_RESERVE,
            quantity_delta=-quantity,  # Negative because stock is being reserved
            stock_before=product.stock,
            stock_after=product.stock,  # Physical stock unchanged during reservation
            reason=f"Stock reserved for session {session_id}",
            performed_by_id=user_id,
        )

        return reservation

    @classmethod
    @transaction.atomic
    def release_reservation(cls, reservation_id: int) -> None:
        """
        Release a stock reservation.

        Marks a reservation as released by setting consumed=True. This is called when:
        - Customer abandons checkout
        - Reservation expires (via cleanup_expired_reservations)
        - Payment fails

        The method validates that the reservation exists and is not already consumed
        before releasing it. It logs the operation to StockLog for audit purposes.

        Note: This method does NOT restore physical stock because reservations don't
        actually decrement stock - they only reserve it. The stock_after in the log
        will equal stock_before since no physical stock change occurs.

        Args:
            reservation_id: ID of the reservation to release

        Raises:
            StockReservationError: If reservation doesn't exist or is already consumed

        Example:
            >>> StockManager.release_reservation(reservation_id=123)
            # Reservation marked as consumed, audit log created
        """
        # Fetch the reservation with related product for logging
        try:
            reservation = StockReservation.objects.select_related(
                "product"
            ).get(id=reservation_id)
        except StockReservation.DoesNotExist:
            raise StockReservationError(
                f"Reservation {reservation_id} not found"
            )

        # Validate reservation is not already consumed
        if reservation.consumed:
            raise StockReservationError(
                f"Reservation {reservation_id} is already consumed"
            )

        # Mark reservation as released (consumed=True indicates it's no longer active)
        # Note: We use consumed=True to mark it as released because the reservation
        # is no longer active. An alternative would be to add a separate 'released'
        # field, but using consumed=True keeps the model simpler.
        reservation.consumed = True
        reservation.save(update_fields=["consumed", "updated_at"])

        # Get current product stock for logging
        product = reservation.product

        # Log the release operation for audit trail
        # Note: stock_before and stock_after are the same because releasing a
        # reservation doesn't change physical stock - it only makes reserved
        # stock available again for other customers
        StockLog.objects.create(
            product=product,
            order=reservation.order,  # Will be None if no order was created
            operation_type=StockLog.OPERATION_RELEASE,
            quantity_delta=reservation.quantity,  # Positive because stock is being freed
            stock_before=product.stock,
            stock_after=product.stock,  # Physical stock unchanged
            reason=f"Reservation {reservation_id} released for session {reservation.session_id}",
            performed_by=reservation.reserved_by,
        )

    @classmethod
    @transaction.atomic
    def convert_reservation_to_sale(
        cls, reservation_id: int, order_id: int
    ) -> None:
        """
        Convert reservation to actual stock decrement.

        This method is called when payment succeeds and converts a temporary
        stock reservation into a permanent stock decrement. It atomically:
        1. Locks the product row to prevent race conditions
        2. Decrements the product's physical stock
        3. Marks the reservation as consumed
        4. Links the reservation to the order
        5. Logs the operation to StockLog for audit trail

        The operation is atomic - if any step fails, all changes are rolled back.
        This ensures data consistency and prevents stock discrepancies.

        Args:
            reservation_id: ID of the reservation to convert
            order_id: ID of the order that consumed this reservation

        Raises:
            StockReservationError: If reservation doesn't exist, is already consumed,
                                  or is expired
            InsufficientStockError: If product stock is insufficient (shouldn't happen
                                   if reservation was valid, but checked for safety)

        Example:
            >>> # After payment succeeds
            >>> StockManager.convert_reservation_to_sale(
            ...     reservation_id=123,
            ...     order_id=456
            ... )
            # Reservation marked as consumed, stock decremented, audit log created
        """
        # Fetch the reservation with related product
        # We need select_related to avoid an extra query when accessing product
        try:
            reservation = StockReservation.objects.select_related(
                "product"
            ).get(id=reservation_id)
        except StockReservation.DoesNotExist:
            raise StockReservationError(
                f"Reservation {reservation_id} not found"
            )

        # Validate reservation is not already consumed
        if reservation.consumed:
            raise StockReservationError(
                f"Reservation {reservation_id} is already consumed"
            )

        # Validate reservation has not expired
        # While expired reservations should be cleaned up, we check here for safety
        if reservation.is_expired:
            raise StockReservationError(
                f"Reservation {reservation_id} has expired at {reservation.expires_at}"
            )

        # Lock the product row using SELECT FOR UPDATE to prevent race conditions
        # This ensures no other transaction can modify the product's stock
        # until this transaction completes
        product = Product.objects.select_for_update().get(
            id=reservation.product_id
        )

        # Validate sufficient stock
        # This should always pass if the reservation was valid, but we check
        # for safety in case stock was manually adjusted
        if product.stock < reservation.quantity:
            raise InsufficientStockError(
                product_id=product.id,
                available=product.stock,
                requested=reservation.quantity,
            )

        # Store stock before decrement for audit log
        stock_before = product.stock

        # Atomically decrement the product's physical stock
        product.stock -= reservation.quantity

        # Set change reason to identify this as a StockManager operation
        # This prevents the signal handler from creating duplicate logs
        product._change_reason = "StockManager: convert_reservation_to_sale"
        product.save(update_fields=["stock", "updated_at"])

        # Mark reservation as consumed and link to order
        reservation.consumed = True
        reservation.order_id = order_id
        reservation.save(update_fields=["consumed", "order_id", "updated_at"])

        # Log the operation to StockLog for audit trail
        # This creates a permanent record of the stock decrement
        StockLog.objects.create(
            product=product,
            order_id=order_id,
            operation_type=StockLog.OPERATION_DECREMENT,
            quantity_delta=-reservation.quantity,  # Negative because stock decreased
            stock_before=stock_before,
            stock_after=product.stock,
            reason=f"Reservation {reservation_id} converted to sale for order {order_id}",
            performed_by=reservation.reserved_by,
        )

    @classmethod
    @transaction.atomic
    def decrement_stock(
        cls,
        product_id: int,
        quantity: int,
        order_id: int,
        reason: str = "order_created",
    ) -> None:
        """
        Directly decrement stock (for admin operations or direct orders).

        This method is used for direct stock decrements that don't go through
        the reservation system. It's typically used for:
        - Admin-initiated stock adjustments
        - Direct orders that bypass the reservation flow
        - Manual inventory corrections

        The method atomically:
        1. Locks the product row using SELECT FOR UPDATE to prevent race conditions
        2. Validates that sufficient stock is available
        3. Decrements the product's physical stock
        4. Logs the operation to StockLog for audit trail

        Unlike convert_reservation_to_sale, this method doesn't check for or
        consume any reservations - it directly modifies the stock level.

        Args:
            product_id: ID of the product to decrement
            quantity: Number of units to decrement
            order_id: ID of the order associated with this decrement
            reason: Human-readable reason for the decrement (default: "order_created")

        Raises:
            ProductNotFoundError: If product doesn't exist
            InsufficientStockError: If not enough stock available
            ValueError: If quantity is not positive

        Example:
            >>> # Admin manually adjusts stock for an order
            >>> StockManager.decrement_stock(
            ...     product_id=123,
            ...     quantity=5,
            ...     order_id=456,
            ...     reason="Manual order adjustment by admin"
            ... )
            # Stock decremented, audit log created
        """
        # Validate quantity
        if quantity <= 0:
            raise ValueError("Quantity must be positive")

        # Lock the product row using SELECT FOR UPDATE to prevent race conditions
        # This ensures no other transaction can modify the product's stock
        # until this transaction completes
        try:
            product = Product.objects.select_for_update().get(id=product_id)
        except Product.DoesNotExist:
            raise ProductNotFoundError(product_id=product_id)

        # Validate sufficient stock
        # This check is critical to prevent overselling
        if product.stock < quantity:
            raise InsufficientStockError(
                product_id=product_id,
                available=product.stock,
                requested=quantity,
            )

        # Store stock before decrement for audit log
        stock_before = product.stock

        # Atomically decrement the product's physical stock
        # We use direct assignment rather than F() expression here because
        # we've already locked the row with select_for_update
        product.stock -= quantity

        # Set change reason to identify this as a StockManager operation
        # This prevents the signal handler from creating duplicate logs
        product._change_reason = "StockManager: decrement_stock"
        product.save(update_fields=["stock", "updated_at"])

        # Log the operation to StockLog for audit trail
        # This creates a permanent record of the stock decrement with
        # before/after values for complete auditability
        StockLog.objects.create(
            product=product,
            order_id=order_id,
            operation_type=StockLog.OPERATION_DECREMENT,
            quantity_delta=-quantity,  # Negative because stock decreased
            stock_before=stock_before,
            stock_after=product.stock,
            reason=reason,
            performed_by=None,  # No user context for direct decrements
        )

    @classmethod
    @transaction.atomic
    def increment_stock(
        cls,
        product_id: int,
        quantity: int,
        order_id: int,
        reason: str = "order_cancelled",
    ) -> None:
        """
        Increment stock (for cancellations/returns).

        This method is used to restore stock when orders are cancelled or items
        are returned. It's typically used for:
        - Order cancellations (restoring stock to available inventory)
        - Product returns (adding returned items back to stock)
        - Manual inventory corrections (admin adjustments)

        The method atomically:
        1. Locks the product row using SELECT FOR UPDATE to prevent race conditions
        2. Increments the product's physical stock
        3. Logs the operation to StockLog for audit trail

        Unlike decrement_stock, this method does not validate stock levels since
        we're adding stock back. However, it still uses atomic transactions and
        proper locking to ensure data consistency.

        Args:
            product_id: ID of the product to increment
            quantity: Number of units to add back to stock
            order_id: ID of the order associated with this increment
            reason: Human-readable reason for the increment (default: "order_cancelled")

        Raises:
            ProductNotFoundError: If product doesn't exist
            ValueError: If quantity is not positive

        Example:
            >>> # Order is cancelled, restore stock
            >>> StockManager.increment_stock(
            ...     product_id=123,
            ...     quantity=5,
            ...     order_id=456,
            ...     reason="Order cancelled by customer"
            ... )
            # Stock incremented, audit log created
        """
        # Validate quantity
        if quantity <= 0:
            raise ValueError("Quantity must be positive")

        # Lock the product row using SELECT FOR UPDATE to prevent race conditions
        # This ensures no other transaction can modify the product's stock
        # until this transaction completes
        try:
            product = Product.objects.select_for_update().get(id=product_id)
        except Product.DoesNotExist:
            raise ProductNotFoundError(product_id=product_id)

        # Store stock before increment for audit log
        stock_before = product.stock

        # Atomically increment the product's physical stock
        # We use direct assignment rather than F() expression here because
        # we've already locked the row with select_for_update
        product.stock += quantity

        # Set change reason to identify this as a StockManager operation
        # This prevents the signal handler from creating duplicate logs
        product._change_reason = "StockManager: increment_stock"
        product.save(update_fields=["stock", "updated_at"])

        # Log the operation to StockLog for audit trail
        # This creates a permanent record of the stock increment with
        # before/after values for complete auditability
        StockLog.objects.create(
            product=product,
            order_id=order_id,
            operation_type=StockLog.OPERATION_INCREMENT,
            quantity_delta=quantity,  # Positive because stock increased
            stock_before=stock_before,
            stock_after=product.stock,
            reason=reason,
            performed_by=None,  # No user context for direct increments
        )

    @classmethod
    def get_available_stock(cls, product_id: int) -> int:
        """
        Calculate available stock for a product.

        This method calculates the available stock by subtracting active
        reservations from the total physical stock. Available stock represents
        the quantity that can be reserved or purchased by new customers.

        The calculation is:
            available_stock = total_stock - sum(active_reservations.quantity)

        Where active reservations are those that are:
        - Not consumed (consumed=False) - reservation hasn't been converted to sale
        - Not expired (expires_at > now) - reservation is still within TTL

        This method does NOT use SELECT FOR UPDATE because it's a read-only
        operation and doesn't modify any data. It provides a point-in-time
        snapshot of available stock.

        Note: The returned value may become stale immediately in high-concurrency
        scenarios. For atomic operations that require stock validation, use
        reserve_stock() or decrement_stock() which use proper locking.

        Args:
            product_id: ID of the product to check

        Returns:
            int: Number of units available for reservation or purchase

        Raises:
            ProductNotFoundError: If product doesn't exist

        Example:
            >>> # Product has 100 units in stock
            >>> # 20 units are reserved (active reservations)
            >>> # 10 units are in expired reservations (not counted)
            >>> available = StockManager.get_available_stock(product_id=123)
            >>> print(available)
            80  # 100 - 20 = 80 units available

            >>> # Check before attempting to reserve
            >>> if StockManager.get_available_stock(product_id) >= quantity:
            ...     # Proceed with reservation
            ...     reservation = StockManager.reserve_stock(...)
        """
        # Fetch the product to get total stock
        # We don't use select_for_update here because this is a read-only operation
        try:
            product = Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            raise ProductNotFoundError(product_id=product_id)

        # Get current time for expiration check
        now = timezone.now()

        # Calculate sum of active reservation quantities
        # Active reservations are those that are:
        # 1. Not consumed (consumed=False) - not yet converted to actual sale
        # 2. Not expired (expires_at > now) - still within 15-minute TTL
        active_reservations = StockReservation.objects.filter(
            product=product, consumed=False, expires_at__gt=now
        )

        # Sum up all active reservation quantities
        # If no active reservations exist, sum returns 0
        reserved_quantity = sum(
            reservation.quantity for reservation in active_reservations
        )

        # Calculate and return available stock
        # This is the quantity that can be reserved or purchased by new customers
        # Ensure we don't return negative values (can happen if reservations exceed stock)
        available_stock = max(0, product.stock - reserved_quantity)

        return available_stock

    @classmethod
    @transaction.atomic
    def cleanup_expired_reservations(cls) -> int:
        """
        Remove expired reservations and restore available stock.

        This method is designed to be called by a Celery periodic task every
        5 minutes to clean up reservations that have exceeded their TTL
        (Time To Live, default 15 minutes).

        The method finds all reservations where:
        - expires_at < current_time (reservation has expired)
        - consumed = False (reservation hasn't been converted to a sale)

        For each expired reservation, it:
        1. Marks the reservation as released (consumed=True)
        2. Logs the operation to StockLog for audit trail

        Note: This method does NOT restore physical stock because reservations
        don't actually decrement stock - they only reserve it. The stock_after
        in the log will equal stock_before since no physical stock change occurs.

        The operation is atomic - all expired reservations are processed within
        a single database transaction to ensure consistency.

        Returns:
            int: Count of expired reservations that were cleaned up

        Example:
            >>> # Called by Celery periodic task every 5 minutes
            >>> count = StockManager.cleanup_expired_reservations()
            >>> print(f"Cleaned up {count} expired reservations")
            Cleaned up 15 expired reservations

            >>> # Typical Celery task implementation:
            >>> @app.task
            >>> def cleanup_expired_stock_reservations():
            ...     count = StockManager.cleanup_expired_reservations()
            ...     logger.info(f"Cleaned up {count} expired reservations")
            ...     return count
        """
        # Get current time for expiration check
        now = timezone.now()

        # Find all expired reservations that haven't been consumed
        # These are reservations where:
        # 1. expires_at < now (past the 15-minute TTL)
        # 2. consumed = False (not yet converted to sale or released)
        expired_reservations = StockReservation.objects.filter(
            expires_at__lt=now, consumed=False
        ).select_related("product", "reserved_by")

        # Count the reservations before processing
        # We do this before the loop to avoid issues with queryset evaluation
        count = expired_reservations.count()

        # Process each expired reservation
        for reservation in expired_reservations:
            # Mark reservation as released (consumed=True indicates it's no longer active)
            # Note: We use consumed=True to mark it as released because the reservation
            # is no longer active. This is consistent with release_reservation() method.
            reservation.consumed = True
            reservation.save(update_fields=["consumed", "updated_at"])

            # Get current product stock for logging
            product = reservation.product

            # Log the cleanup operation for audit trail
            # Note: stock_before and stock_after are the same because releasing a
            # reservation doesn't change physical stock - it only makes reserved
            # stock available again for other customers
            StockLog.objects.create(
                product=product,
                order=reservation.order,  # Will be None for expired reservations
                operation_type=StockLog.OPERATION_RELEASE,
                quantity_delta=reservation.quantity,  # Positive because stock is being freed
                stock_before=product.stock,
                stock_after=product.stock,  # Physical stock unchanged
                reason=f"Expired reservation {reservation.id} auto-released (expired at {reservation.expires_at})",
                performed_by=reservation.reserved_by,
            )

        # Return count of cleaned reservations
        return count
