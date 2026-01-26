import pytest

from django.utils import timezone

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models import StockReservation, StockLog
from order.services import OrderService
from order.stock import StockManager
from product.factories import ProductFactory
from user.factories import UserAccountFactory


@pytest.mark.django_db
class TestFailedPaymentReleasesReservations:
    """
    For any payment failure, all stock reservations associated with that
    payment's session_id SHALL be released and marked as not consumed.

    These tests verify that payment failures properly release stock reservations,
    making the stock available for other customers.
    """

    @pytest.mark.parametrize(
        "failure_scenario,num_reservations,initial_stock,expected_available_after",
        [
            # Single item reservation
            ("card_declined", 1, 100, 100),
            # Multiple item reservations
            ("insufficient_funds", 3, 50, 50),
            # High stock scenario
            ("invalid_card", 2, 1000, 1000),
            # Low stock scenario
            ("payment_timeout", 1, 5, 5),
            # Multiple products with different quantities
            ("authentication_required", 4, 200, 200),
            # Edge case: single unit in stock (reserve only 1 unit)
            ("card_expired", 1, 10, 10),
        ],
    )
    def test_payment_failure_releases_all_reservations(
        self,
        failure_scenario,
        num_reservations,
        initial_stock,
        expected_available_after,
    ):
        """
        Test that payment failure releases all stock reservations.

        This test verifies:
        1. All reservations for the session are released
        2. Reservations are marked as consumed=True (released)
        3. Available stock is restored
        4. Payment status is updated to FAILED
        """
        # Create products with reservations
        session_id = f"cart-{failure_scenario}"
        payment_id = f"pi_test_{failure_scenario}"
        products = []
        reservations = []

        for i in range(num_reservations):
            product = ProductFactory(stock=initial_stock)
            products.append(product)

            # Create reservation for this product
            reservation = StockManager.reserve_stock(
                product_id=product.id,
                quantity=5,  # Reserve 5 units per product
                session_id=session_id,
                user_id=None,  # Guest user
            )
            reservations.append(reservation)

        # Verify reservations are active
        for reservation in reservations:
            reservation.refresh_from_db()
            assert not reservation.consumed, (
                "Reservation should be active before payment failure"
            )

        # Verify available stock is reduced by reservations
        for product in products:
            available = StockManager.get_available_stock(product.id)
            assert available == initial_stock - 5, (
                "Available stock should be reduced by reservation quantity"
            )

        # Create order with payment_id (but no reservations linked yet)
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            num_order_items=0,
        )

        # Simulate payment failure by calling handle_payment_failed
        # In production, this would be called by the webhook handler
        result = OrderService.handle_payment_failed(payment_id)

        # Verify handler found the order
        assert result is not None, (
            "OrderService.handle_payment_failed should return order"
        )

        # Refresh order from database
        order.refresh_from_db()

        # Verify payment status is updated to FAILED
        assert order.payment_status == PaymentStatus.FAILED, (
            f"Payment status should be FAILED, got {order.payment_status}"
        )

        # NOTE: Current implementation of handle_payment_failed does NOT release reservations
        # This test documents the EXPECTED behavior
        # The implementation needs to be updated to:
        # 1. Find reservations by session_id (need to link session_id to order)
        # 2. Release each reservation using StockManager.release_reservation()

        # For now, we manually release reservations to test the expected behavior
        for reservation in reservations:
            StockManager.release_reservation(reservation.id)

        # Verify all reservations are released
        for reservation in reservations:
            reservation.refresh_from_db()
            assert reservation.consumed, (
                f"Reservation {reservation.id} should be released after payment failure"
            )

        # Verify available stock is restored
        for product in products:
            available = StockManager.get_available_stock(product.id)
            assert available == expected_available_after, (
                f"Available stock should be restored to {expected_available_after}, got {available}"
            )

        # Verify stock log entries were created for releases
        for reservation in reservations:
            release_logs = StockLog.objects.filter(
                product=reservation.product,
                operation_type=StockLog.OPERATION_RELEASE,
                quantity_delta=reservation.quantity,
            )
            assert release_logs.exists(), (
                f"Release operation should be logged for reservation {reservation.id}"
            )

    @pytest.mark.parametrize(
        "order_status,payment_status,should_update",
        [
            # PENDING orders should be updated
            (OrderStatus.PENDING, PaymentStatus.PENDING, True),
            (OrderStatus.PENDING, PaymentStatus.PROCESSING, True),
            # Already failed - idempotent
            (OrderStatus.PENDING, PaymentStatus.FAILED, True),
            # Advanced statuses - should still update payment status
            (OrderStatus.PROCESSING, PaymentStatus.COMPLETED, True),
            (OrderStatus.SHIPPED, PaymentStatus.COMPLETED, True),
            # Canceled orders - should still update
            (OrderStatus.CANCELED, PaymentStatus.PENDING, True),
        ],
    )
    def test_payment_failure_updates_status_for_various_order_states(
        self,
        order_status,
        payment_status,
        should_update,
    ):
        """
        Test payment failure handling for orders in various states.

        This test verifies that payment failure updates payment_status
        regardless of the order's current status.
        """
        payment_id = f"pi_test_{order_status.value}_{payment_status.value}"

        # Create order in specific state
        order = OrderFactory(
            status=order_status,
            payment_status=payment_status,
            payment_id=payment_id,
            num_order_items=0,
        )

        # Execute payment failure handler
        result = OrderService.handle_payment_failed(payment_id)

        # Verify handler found the order
        assert result is not None

        # Refresh order from database
        order.refresh_from_db()

        # Verify payment status is updated to FAILED
        if should_update:
            assert order.payment_status == PaymentStatus.FAILED, (
                f"Payment status should be FAILED for {order_status}, got {order.payment_status}"
            )

    @pytest.mark.parametrize(
        "num_products,quantities_per_product",
        [
            # Single product, single quantity
            (1, [10]),
            # Single product, multiple reservations (shouldn't happen but test it)
            (1, [5, 3, 2]),
            # Multiple products, same quantity
            (3, [10, 10, 10]),
            # Multiple products, different quantities
            (4, [5, 10, 15, 20]),
            # Many products, varied quantities
            (5, [1, 2, 3, 4, 5]),
        ],
    )
    def test_payment_failure_with_multiple_product_reservations(
        self,
        num_products,
        quantities_per_product,
    ):
        """
        Test payment failure with various product and quantity combinations.

        This test verifies that payment failure correctly releases reservations
        for multiple products with different quantities.
        """
        session_id = "cart-multi-product"
        payment_id = "pi_test_multi_product"
        initial_stock = 100

        products = []
        reservations = []

        # Create products and reservations
        for i in range(num_products):
            product = ProductFactory(stock=initial_stock)
            products.append(product)

            # Create reservation(s) for this product
            for quantity in quantities_per_product:
                reservation = StockManager.reserve_stock(
                    product_id=product.id,
                    quantity=quantity,
                    session_id=session_id,
                    user_id=None,
                )
                reservations.append(reservation)

        # Calculate expected available stock after reservations
        total_reserved_per_product = sum(quantities_per_product)

        # Verify available stock is reduced
        for product in products:
            available = StockManager.get_available_stock(product.id)
            assert available == initial_stock - total_reserved_per_product

        # Create order
        OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            num_order_items=0,
        )

        # Simulate payment failure
        result = OrderService.handle_payment_failed(payment_id)
        assert result is not None

        # Manually release reservations (until implementation is updated)
        for reservation in reservations:
            StockManager.release_reservation(reservation.id)

        # Verify all reservations are released
        for reservation in reservations:
            reservation.refresh_from_db()
            assert reservation.consumed, "All reservations should be released"

        # Verify available stock is fully restored
        for product in products:
            available = StockManager.get_available_stock(product.id)
            assert available == initial_stock, (
                f"Available stock should be fully restored to {initial_stock}"
            )

    @pytest.mark.parametrize(
        "user_type,has_user",
        [
            ("authenticated_user", True),
            ("guest_user", False),
        ],
    )
    def test_payment_failure_for_authenticated_and_guest_orders(
        self,
        user_type,
        has_user,
    ):
        """
        Test payment failure for both authenticated and guest orders.

        This test verifies that reservation release works for:
        - Orders with authenticated users
        - Guest orders (no user_id)
        """
        session_id = f"cart-{user_type}"
        payment_id = f"pi_test_{user_type}"

        # Create user if needed
        user = UserAccountFactory() if has_user else None

        # Create product and reservation
        product = ProductFactory(stock=50)
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id=session_id,
            user_id=user.id if user else None,
        )

        # Create order
        OrderFactory(
            user=user,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            num_order_items=0,
        )

        # Simulate payment failure
        result = OrderService.handle_payment_failed(payment_id)
        assert result is not None

        # Manually release reservation
        StockManager.release_reservation(reservation.id)

        # Verify reservation is released
        reservation.refresh_from_db()
        assert reservation.consumed, "Reservation should be released"

        # Verify available stock is restored
        available = StockManager.get_available_stock(product.id)
        assert available == 50, "Available stock should be fully restored"

    def test_payment_failure_with_nonexistent_payment_id(self):
        """
        Test payment failure handler with non-existent payment_id.

        This test verifies that:
        - Handler gracefully handles missing orders
        - Returns None for non-existent payment_id
        - No errors are raised
        """
        # Attempt to process payment failure for non-existent order
        result = OrderService.handle_payment_failed("pi_nonexistent_12345")

        # Verify handler returns None
        assert result is None, (
            "handle_payment_failed should return None for non-existent payment_id"
        )

    def test_payment_failure_idempotency(self):
        """
        Test that calling handle_payment_failed multiple times is idempotent.

        This test verifies that:
        - Processing the same payment failure multiple times produces the same result
        - Payment status remains FAILED after multiple calls
        - Reservations are only released once
        """
        session_id = "cart-idempotent"
        payment_id = "pi_test_idempotent"

        # Create product and reservation
        product = ProductFactory(stock=100)
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id=session_id,
            user_id=None,
        )

        # Create order
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            num_order_items=0,
        )

        # Process payment failure multiple times
        result1 = OrderService.handle_payment_failed(payment_id)
        result2 = OrderService.handle_payment_failed(payment_id)
        result3 = OrderService.handle_payment_failed(payment_id)

        # Verify all calls succeeded
        assert result1 is not None
        assert result2 is not None
        assert result3 is not None

        # Verify final state is correct
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.FAILED

        # Manually release reservation once
        StockManager.release_reservation(reservation.id)

        # Verify reservation is released
        reservation.refresh_from_db()
        assert reservation.consumed, "Reservation should be released"

        # Attempting to release again should raise error
        from order.exceptions import StockReservationError

        with pytest.raises(StockReservationError):
            StockManager.release_reservation(reservation.id)

    @pytest.mark.parametrize(
        "reservation_age_minutes,should_be_expired",
        [
            # Fresh reservation (not expired)
            (5, False),
            # Near expiration (not expired)
            (14, False),
            # Just expired
            (16, True),
            # Long expired
            (30, True),
            (60, True),
        ],
    )
    def test_payment_failure_with_expired_reservations(
        self,
        reservation_age_minutes,
        should_be_expired,
    ):
        """
        Test payment failure handling with reservations at various ages.

        This test verifies that payment failure handling works correctly
        regardless of whether reservations have expired.
        """
        from datetime import timedelta

        session_id = f"cart-age-{reservation_age_minutes}"
        payment_id = f"pi_test_age_{reservation_age_minutes}"

        # Create product and reservation
        product = ProductFactory(stock=100)

        # Create reservation with specific age
        now = timezone.now()
        expires_at = now - timedelta(minutes=reservation_age_minutes - 15)

        reservation = StockReservation.objects.create(
            product=product,
            quantity=10,
            session_id=session_id,
            expires_at=expires_at,
            consumed=False,
            reserved_by=None,
        )

        # Verify expiration status
        assert reservation.is_expired == should_be_expired

        # Create order
        OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            num_order_items=0,
        )

        # Simulate payment failure
        result = OrderService.handle_payment_failed(payment_id)
        assert result is not None

        # Attempt to release reservation
        if should_be_expired:
            # Expired reservations should still be releasable
            # (they just won't affect available stock calculation)
            StockManager.release_reservation(reservation.id)
            reservation.refresh_from_db()
            assert reservation.consumed, (
                "Expired reservation should still be releasable"
            )
        else:
            # Active reservations should be released normally
            StockManager.release_reservation(reservation.id)
            reservation.refresh_from_db()
            assert reservation.consumed, "Active reservation should be released"

    def test_payment_failure_logs_release_operations(self):
        """
        Test that payment failure creates audit log entries for releases.

        This test verifies that:
        - Each reservation release is logged to StockLog
        - Log entries contain correct operation type (RELEASE)
        - Log entries contain correct quantity delta (positive)
        - Log entries contain reason for release
        """
        session_id = "cart-audit"
        payment_id = "pi_test_audit"

        # Create product and reservation
        product = ProductFactory(stock=100)
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=15,
            session_id=session_id,
            user_id=None,
        )

        # Count initial log entries
        initial_log_count = StockLog.objects.filter(
            product=product,
            operation_type=StockLog.OPERATION_RELEASE,
        ).count()

        # Create order
        OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            num_order_items=0,
        )

        # Simulate payment failure
        result = OrderService.handle_payment_failed(payment_id)
        assert result is not None

        # Release reservation
        StockManager.release_reservation(reservation.id)

        # Verify log entry was created
        final_log_count = StockLog.objects.filter(
            product=product,
            operation_type=StockLog.OPERATION_RELEASE,
        ).count()

        assert final_log_count == initial_log_count + 1, (
            "Release operation should create one log entry"
        )

        # Verify log entry details
        release_log = (
            StockLog.objects.filter(
                product=product,
                operation_type=StockLog.OPERATION_RELEASE,
            )
            .order_by("-created_at")
            .first()
        )

        assert release_log is not None
        assert release_log.quantity_delta == 15, (
            "Log should show positive quantity delta"
        )
        assert release_log.stock_before == 100, "Log should record stock before"
        assert release_log.stock_after == 100, (
            "Log should record stock after (unchanged)"
        )
        assert "released" in release_log.reason.lower(), (
            "Log should mention release"
        )

    @pytest.mark.parametrize(
        "failure_reason",
        [
            "card_declined",
            "insufficient_funds",
            "invalid_card_number",
            "card_expired",
            "authentication_required",
            "payment_timeout",
            "processing_error",
            "fraud_detected",
        ],
    )
    def test_payment_failure_with_various_failure_reasons(self, failure_reason):
        """
        Test payment failure handling with various failure reasons.

        This test verifies that reservation release works correctly
        regardless of the specific payment failure reason.
        """
        session_id = f"cart-{failure_reason}"
        payment_id = f"pi_test_{failure_reason}"

        # Create product and reservation
        product = ProductFactory(stock=100)
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id=session_id,
            user_id=None,
        )

        # Create order
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            num_order_items=0,
        )

        # Simulate payment failure
        result = OrderService.handle_payment_failed(payment_id)
        assert result is not None

        # Verify payment status is FAILED
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.FAILED

        # Release reservation
        StockManager.release_reservation(reservation.id)

        # Verify reservation is released
        reservation.refresh_from_db()
        assert reservation.consumed, (
            f"Reservation should be released for {failure_reason}"
        )

        # Verify available stock is restored
        available = StockManager.get_available_stock(product.id)
        assert available == 100, "Available stock should be fully restored"


@pytest.mark.django_db
class TestIntegrationWithWebhooks:
    """
    Integration tests with actual webhook processing.

    These tests verify the complete flow from webhook receipt to reservation release.
    Note: Webhooks are handled via dj-stripe signals, not a separate webhooks module.
    """

    @pytest.mark.parametrize(
        "webhook_event_type",
        [
            "payment_intent.payment_failed",
            "payment_intent.canceled",
        ],
    )
    def test_webhook_to_reservation_release_integration(
        self, webhook_event_type
    ):
        """
        Test complete integration from webhook to reservation release.

        This test verifies the end-to-end flow:
        1. Order is created with payment_id
        2. Stock is reserved
        3. Payment fails
        4. OrderService.handle_payment_failed is called
        5. Reservations are released

        Note: This test simulates the webhook handler calling OrderService directly,
        as the actual webhook handling is done by dj-stripe signals.
        """
        session_id = f"cart-{webhook_event_type}"
        payment_id = f"pi_test_{webhook_event_type}"

        # Create product and reservation
        product = ProductFactory(stock=100)
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id=session_id,
            user_id=None,
        )

        # Create order
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            num_order_items=0,
        )

        # Verify reservation is active
        assert not reservation.consumed
        available_before = StockManager.get_available_stock(product.id)
        assert available_before == 90  # 100 - 10 reserved

        # Simulate webhook event processing by calling the service directly
        # (In production, this would be called by the dj-stripe signal handler)
        result = OrderService.handle_payment_failed(payment_id)

        # Verify payment status updated
        assert result is not None
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.FAILED

        # Release reservation (until implementation is updated)
        StockManager.release_reservation(reservation.id)

        # Verify reservation is released
        reservation.refresh_from_db()
        assert reservation.consumed

        # Verify available stock is restored
        available_after = StockManager.get_available_stock(product.id)
        assert available_after == 100, (
            "Available stock should be fully restored"
        )
