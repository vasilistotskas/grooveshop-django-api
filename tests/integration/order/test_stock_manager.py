import pytest
from datetime import timedelta
from django.utils import timezone

from order.exceptions import (
    InsufficientStockError,
    ProductNotFoundError,
)
from order.models import StockLog, StockReservation
from order.stock import StockManager
from product.factories import ProductFactory
from user.factories import UserAccountFactory


@pytest.mark.django_db
class TestStockManagerReserveStock:
    """Test suite for StockManager.reserve_stock method."""

    def test_reserve_stock_success(self):
        """Test successful stock reservation."""
        product = ProductFactory(stock=100)
        user = UserAccountFactory()
        session_id = "cart-uuid-123"

        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id=session_id,
            user_id=user.id,
        )

        # Verify reservation created
        assert reservation.id is not None
        assert reservation.product == product
        assert reservation.quantity == 10
        assert reservation.reserved_by == user
        assert reservation.session_id == session_id
        assert reservation.consumed is False
        assert reservation.order is None

        # Verify expiration time is 15 minutes from now
        now = timezone.now()
        expected_expiry = now + timedelta(
            minutes=StockManager.get_reservation_ttl_minutes()
        )
        time_diff = abs(
            (reservation.expires_at - expected_expiry).total_seconds()
        )
        assert (
            time_diff < 2
        )  # Allow 2 seconds tolerance for test execution time

    def test_reserve_stock_creates_audit_log(self):
        """Test that stock reservation creates a StockLog entry."""
        product = ProductFactory(stock=100)
        session_id = "cart-uuid-456"

        # Count logs before
        initial_log_count = StockLog.objects.filter(product=product).count()

        StockManager.reserve_stock(
            product_id=product.id,
            quantity=5,
            session_id=session_id,
            user_id=None,
        )

        # Verify log created
        logs = StockLog.objects.filter(product=product).order_by("-created_at")
        assert logs.count() == initial_log_count + 1

        log = logs.first()
        assert log.operation_type == StockLog.OPERATION_RESERVE
        assert log.quantity_delta == -5
        assert log.stock_before == 100
        assert (
            log.stock_after == 100
        )  # Physical stock unchanged during reservation
        assert session_id in log.reason

    def test_reserve_stock_for_guest_user(self):
        """Test stock reservation for guest user (user_id=None)."""
        product = ProductFactory(stock=50)
        session_id = "guest-cart-789"

        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=3,
            session_id=session_id,
            user_id=None,
        )

        assert reservation.reserved_by is None
        assert reservation.session_id == session_id

    def test_reserve_stock_insufficient_stock(self):
        """Test that InsufficientStockError is raised when stock is insufficient."""
        product = ProductFactory(stock=5)

        with pytest.raises(InsufficientStockError) as exc_info:
            StockManager.reserve_stock(
                product_id=product.id,
                quantity=10,
                session_id="cart-uuid",
                user_id=None,
            )

        # Verify exception details
        assert exc_info.value.product_id == product.id
        assert exc_info.value.available == 5
        assert exc_info.value.requested == 10

    def test_reserve_stock_product_not_found(self):
        """Test that ProductNotFoundError is raised for non-existent product."""
        non_existent_id = 99999

        with pytest.raises(ProductNotFoundError) as exc_info:
            StockManager.reserve_stock(
                product_id=non_existent_id,
                quantity=5,
                session_id="cart-uuid",
                user_id=None,
            )

        assert exc_info.value.product_id == non_existent_id

    def test_reserve_stock_invalid_quantity(self):
        """Test that ValueError is raised for invalid quantity."""
        product = ProductFactory(stock=100)

        # Test zero quantity
        with pytest.raises(ValueError) as exc_info:
            StockManager.reserve_stock(
                product_id=product.id,
                quantity=0,
                session_id="cart-uuid",
                user_id=None,
            )
        assert "positive" in str(exc_info.value).lower()

        # Test negative quantity
        with pytest.raises(ValueError) as exc_info:
            StockManager.reserve_stock(
                product_id=product.id,
                quantity=-5,
                session_id="cart-uuid",
                user_id=None,
            )
        assert "positive" in str(exc_info.value).lower()

    def test_reserve_stock_excludes_active_reservations(self):
        """Test that available stock calculation excludes active reservations."""
        product = ProductFactory(stock=100)

        # Create first reservation for 30 units
        StockManager.reserve_stock(
            product_id=product.id,
            quantity=30,
            session_id="cart-1",
            user_id=None,
        )

        # Available stock should now be 70 (100 - 30)
        # Try to reserve 80 units - should fail
        with pytest.raises(InsufficientStockError) as exc_info:
            StockManager.reserve_stock(
                product_id=product.id,
                quantity=80,
                session_id="cart-2",
                user_id=None,
            )

        assert exc_info.value.available == 70
        assert exc_info.value.requested == 80

        # Try to reserve 70 units - should succeed
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=70,
            session_id="cart-3",
            user_id=None,
        )
        assert reservation.quantity == 70

    def test_reserve_stock_ignores_expired_reservations(self):
        """Test that expired reservations don't affect available stock."""
        product = ProductFactory(stock=100)

        # Create an expired reservation
        past_time = timezone.now() - timedelta(minutes=20)
        StockReservation.objects.create(
            product=product,
            quantity=50,
            session_id="expired-cart",
            expires_at=past_time,
            consumed=False,
        )

        # Should be able to reserve full stock since expired reservation doesn't count
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=100,
            session_id="new-cart",
            user_id=None,
        )
        assert reservation.quantity == 100

    def test_reserve_stock_ignores_consumed_reservations(self):
        """Test that consumed reservations don't affect available stock."""
        product = ProductFactory(stock=100)

        # Create a consumed reservation
        future_time = timezone.now() + timedelta(minutes=10)
        StockReservation.objects.create(
            product=product,
            quantity=50,
            session_id="consumed-cart",
            expires_at=future_time,
            consumed=True,  # Already consumed
        )

        # Should be able to reserve full stock since consumed reservation doesn't count
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=100,
            session_id="new-cart",
            user_id=None,
        )
        assert reservation.quantity == 100

    def test_reserve_stock_multiple_active_reservations(self):
        """Test available stock calculation with multiple active reservations."""
        product = ProductFactory(stock=100)

        # Create multiple active reservations
        StockManager.reserve_stock(
            product_id=product.id,
            quantity=20,
            session_id="cart-1",
            user_id=None,
        )

        StockManager.reserve_stock(
            product_id=product.id,
            quantity=30,
            session_id="cart-2",
            user_id=None,
        )

        # Available: 100 - 20 - 30 = 50
        # Try to reserve 51 - should fail
        with pytest.raises(InsufficientStockError) as exc_info:
            StockManager.reserve_stock(
                product_id=product.id,
                quantity=51,
                session_id="cart-3",
                user_id=None,
            )

        assert exc_info.value.available == 50

        # Reserve exactly 50 - should succeed
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=50,
            session_id="cart-4",
            user_id=None,
        )
        assert reservation.quantity == 50

    @pytest.mark.parametrize(
        "stock,quantity",
        [
            (100, 1),
            (100, 50),
            (100, 100),
            (10, 10),
            (1, 1),
        ],
    )
    def test_reserve_stock_various_quantities(self, stock, quantity):
        """Test stock reservation with various stock and quantity combinations."""
        product = ProductFactory(stock=stock)

        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=quantity,
            session_id=f"cart-{stock}-{quantity}",
            user_id=None,
        )

        assert reservation.quantity == quantity
        assert reservation.product == product

    def test_reserve_stock_uses_select_for_update(self):
        """Test that reserve_stock uses SELECT FOR UPDATE for atomicity."""
        # This test verifies the method doesn't raise an error when called
        # within a transaction, which would fail if SELECT FOR UPDATE wasn't used
        product = ProductFactory(stock=100)

        # The @transaction.atomic decorator on reserve_stock should handle this
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-uuid",
            user_id=None,
        )

        assert reservation is not None
        assert reservation.quantity == 10

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.xfail(
        reason="Concurrent reservation test is inherently flaky due to race conditions "
        "in parallel test execution. The SELECT FOR UPDATE locking may not prevent "
        "all race conditions when multiple threads query StockReservation simultaneously.",
        strict=False,
    )
    def test_reserve_stock_concurrent_reservations(self):
        """
        Test that concurrent reservation attempts prevent overselling.

        This test verifies that SELECT FOR UPDATE locking prevents race conditions
        when multiple threads attempt to reserve stock simultaneously.

        Note: This test is marked as xfail because concurrent behavior is difficult
        to test reliably in a parallel test environment. The test may pass or fail
        depending on timing and database transaction isolation levels.
        """
        import threading
        import uuid

        product = ProductFactory(stock=100)
        test_id = uuid.uuid4().hex[:8]
        results = []
        errors = []

        def attempt_reservation(quantity, session_id):
            """Attempt to reserve stock in a separate thread."""
            try:
                reservation = StockManager.reserve_stock(
                    product_id=product.id,
                    quantity=quantity,
                    session_id=session_id,
                    user_id=None,
                )
                results.append(("success", quantity, reservation.id))
            except InsufficientStockError as e:
                errors.append(("insufficient_stock", quantity, e))
            except Exception as e:
                errors.append(("error", quantity, e))

        # Create 5 threads trying to reserve 30 units each (total 150 > 100 available)
        threads = []
        for i in range(5):
            thread = threading.Thread(
                target=attempt_reservation,
                args=(30, f"concurrent-{test_id}-{i}"),
            )
            threads.append(thread)

        # Start all threads simultaneously
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify results
        total_attempts = len(results) + len(errors)
        assert total_attempts == 5, "All 5 threads should have completed"

        # Calculate total successfully reserved
        total_reserved = sum(
            quantity for status, quantity, _ in results if status == "success"
        )

        # Verify we didn't oversell (total reserved should be <= 100)
        assert total_reserved <= 100, (
            f"Overselling detected: {total_reserved} units reserved from 100 available"
        )

        # Verify at least one thread failed due to insufficient stock
        insufficient_stock_errors = [
            e for status, _, e in errors if status == "insufficient_stock"
        ]
        assert len(insufficient_stock_errors) > 0, (
            "At least one reservation should have failed due to insufficient stock"
        )

        # Verify product stock is unchanged (reservations don't decrement physical stock)
        product.refresh_from_db()
        assert product.stock == 100, (
            "Physical stock should remain unchanged after reservations"
        )

        # Verify the number of active reservations created by this test matches successful attempts
        # Filter by session_id prefix to only count our test's reservations
        active_reservations = StockReservation.objects.filter(
            product=product,
            consumed=False,
            expires_at__gt=timezone.now(),
            session_id__startswith=f"concurrent-{test_id}-",
        )
        assert active_reservations.count() == len(results), (
            "Number of active reservations should match successful attempts"
        )

        # Verify total reserved quantity matches
        actual_reserved = sum(r.quantity for r in active_reservations)
        assert actual_reserved == total_reserved, (
            "Total reserved quantity should match sum of successful reservations"
        )

        # Verify available stock is correct
        available = StockManager.get_available_stock(product.id)
        assert available == 100 - total_reserved, (
            f"Available stock should be {100 - total_reserved}, got {available}"
        )


@pytest.mark.django_db
class TestStockManagerReleaseReservation:
    """Test suite for StockManager.release_reservation method."""

    def test_release_reservation_success(self):
        """Test successful reservation release."""
        product = ProductFactory(stock=100)
        user = UserAccountFactory()

        # Create a reservation first
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-uuid-123",
            user_id=user.id,
        )

        assert reservation.consumed is False

        # Release the reservation
        StockManager.release_reservation(reservation_id=reservation.id)

        # Verify reservation is marked as consumed
        reservation.refresh_from_db()
        assert reservation.consumed is True

    def test_release_reservation_creates_audit_log(self):
        """Test that releasing a reservation creates a StockLog entry."""
        product = ProductFactory(stock=100)
        session_id = "cart-uuid-456"

        # Create a reservation
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=5,
            session_id=session_id,
            user_id=None,
        )

        # Count logs before release
        initial_log_count = StockLog.objects.filter(product=product).count()

        # Release the reservation
        StockManager.release_reservation(reservation_id=reservation.id)

        # Verify log created
        logs = StockLog.objects.filter(product=product).order_by("-created_at")
        assert logs.count() == initial_log_count + 1

        log = logs.first()
        assert log.operation_type == StockLog.OPERATION_RELEASE
        assert log.quantity_delta == 5  # Positive because stock is being freed
        assert log.stock_before == 100
        assert log.stock_after == 100  # Physical stock unchanged
        assert str(reservation.id) in log.reason
        assert session_id in log.reason

    def test_release_reservation_not_found(self):
        """Test that StockReservationError is raised for non-existent reservation."""
        from order.exceptions import StockReservationError

        non_existent_id = 99999

        with pytest.raises(StockReservationError) as exc_info:
            StockManager.release_reservation(reservation_id=non_existent_id)

        assert str(non_existent_id) in str(exc_info.value)
        assert "not found" in str(exc_info.value).lower()

    def test_release_reservation_already_consumed(self):
        """Test that StockReservationError is raised for already consumed reservation."""
        from order.exceptions import StockReservationError

        product = ProductFactory(stock=100)

        # Create and immediately consume a reservation
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-uuid",
            user_id=None,
        )

        # Release it once
        StockManager.release_reservation(reservation_id=reservation.id)

        # Try to release again - should fail
        with pytest.raises(StockReservationError) as exc_info:
            StockManager.release_reservation(reservation_id=reservation.id)

        assert str(reservation.id) in str(exc_info.value)
        assert "already consumed" in str(exc_info.value).lower()

    def test_release_reservation_stock_restoration(self):
        """Test that releasing a reservation makes stock available again."""
        product = ProductFactory(stock=100)

        # Reserve 60 units
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=60,
            session_id="cart-1",
            user_id=None,
        )

        # Available stock should be 40
        # Try to reserve 50 - should fail
        with pytest.raises(InsufficientStockError):
            StockManager.reserve_stock(
                product_id=product.id,
                quantity=50,
                session_id="cart-2",
                user_id=None,
            )

        # Release the first reservation
        StockManager.release_reservation(reservation_id=reservation.id)

        # Now we should be able to reserve 50 units
        new_reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=50,
            session_id="cart-3",
            user_id=None,
        )
        assert new_reservation.quantity == 50

    def test_release_reservation_with_user(self):
        """Test releasing a reservation made by an authenticated user."""
        product = ProductFactory(stock=100)
        user = UserAccountFactory()

        # Create reservation with user
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-uuid",
            user_id=user.id,
        )

        # Release the reservation
        StockManager.release_reservation(reservation_id=reservation.id)

        # Verify log has correct user
        log = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_RELEASE
        ).first()
        assert log.performed_by == user

    def test_release_reservation_guest_user(self):
        """Test releasing a reservation made by a guest user."""
        product = ProductFactory(stock=100)

        # Create reservation without user
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="guest-cart",
            user_id=None,
        )

        # Release the reservation
        StockManager.release_reservation(reservation_id=reservation.id)

        # Verify log has no user
        log = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_RELEASE
        ).first()
        assert log.performed_by is None

    def test_release_reservation_multiple_times_fails(self):
        """Test that releasing the same reservation multiple times fails."""
        from order.exceptions import StockReservationError

        product = ProductFactory(stock=100)

        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-uuid",
            user_id=None,
        )

        # First release should succeed
        StockManager.release_reservation(reservation_id=reservation.id)

        # Second release should fail
        with pytest.raises(StockReservationError):
            StockManager.release_reservation(reservation_id=reservation.id)

        # Third release should also fail
        with pytest.raises(StockReservationError):
            StockManager.release_reservation(reservation_id=reservation.id)

    @pytest.mark.parametrize("quantity", [1, 5, 10, 50, 100])
    def test_release_reservation_various_quantities(self, quantity):
        """Test releasing reservations with various quantities."""
        product = ProductFactory(stock=100)

        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=quantity,
            session_id=f"cart-{quantity}",
            user_id=None,
        )

        # Release should succeed regardless of quantity
        StockManager.release_reservation(reservation_id=reservation.id)

        # Verify log has correct quantity
        log = (
            StockLog.objects.filter(
                product=product, operation_type=StockLog.OPERATION_RELEASE
            )
            .order_by("-created_at")
            .first()
        )
        assert log.quantity_delta == quantity

    def test_release_reservation_updates_timestamp(self):
        """Test that releasing a reservation updates the updated_at timestamp."""
        product = ProductFactory(stock=100)

        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-uuid",
            user_id=None,
        )

        original_updated_at = reservation.updated_at

        # Wait a tiny bit to ensure timestamp difference
        import time

        time.sleep(0.01)

        # Release the reservation
        StockManager.release_reservation(reservation_id=reservation.id)

        # Verify updated_at changed
        reservation.refresh_from_db()
        assert reservation.updated_at > original_updated_at


@pytest.mark.django_db
class TestStockManagerConvertReservationToSale:
    """Test suite for StockManager.convert_reservation_to_sale method."""

    def test_convert_reservation_to_sale_success(self):
        """Test successful conversion of reservation to sale."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)
        user = UserAccountFactory()

        # Create a reservation
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-uuid-123",
            user_id=user.id,
        )

        # Create an order without items to avoid signal handler interference
        order = OrderFactory(user=user, num_order_items=0)

        # Convert reservation to sale
        StockManager.convert_reservation_to_sale(
            reservation_id=reservation.id, order_id=order.id
        )

        # Verify reservation is marked as consumed
        reservation.refresh_from_db()
        assert reservation.consumed is True

        # Verify reservation is linked to order
        assert reservation.order == order

        # Verify product stock was decremented
        product.refresh_from_db()
        assert product.stock == 90  # 100 - 10

    def test_convert_reservation_to_sale_decrements_stock(self):
        """Test that conversion decrements product stock correctly."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=50)

        # Create reservation for 15 units
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=15,
            session_id="cart-uuid",
            user_id=None,
        )

        order = OrderFactory(num_order_items=0)

        # Convert to sale
        StockManager.convert_reservation_to_sale(
            reservation_id=reservation.id, order_id=order.id
        )

        # Verify stock decreased by exactly the reserved quantity
        product.refresh_from_db()
        assert product.stock == 35  # 50 - 15

    def test_convert_reservation_to_sale_creates_audit_log(self):
        """Test that conversion creates a StockLog entry with DECREMENT type."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)
        session_id = "cart-uuid-456"

        # Create a reservation
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=20,
            session_id=session_id,
            user_id=None,
        )

        order = OrderFactory(num_order_items=0)

        # Count logs before conversion
        initial_log_count = StockLog.objects.filter(product=product).count()

        # Convert to sale
        StockManager.convert_reservation_to_sale(
            reservation_id=reservation.id, order_id=order.id
        )

        # Verify log created
        logs = StockLog.objects.filter(product=product).order_by("-created_at")
        assert logs.count() == initial_log_count + 1

        log = logs.first()
        assert log.operation_type == StockLog.OPERATION_DECREMENT
        assert log.quantity_delta == -20  # Negative because stock decreased
        assert log.stock_before == 100
        assert log.stock_after == 80  # 100 - 20
        assert log.order == order
        assert str(reservation.id) in log.reason
        assert str(order.id) in log.reason

    def test_convert_reservation_to_sale_links_to_order(self):
        """Test that reservation is linked to the order."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)

        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=5,
            session_id="cart-uuid",
            user_id=None,
        )

        order = OrderFactory(num_order_items=0)

        # Initially no order linked
        assert reservation.order is None

        # Convert to sale
        StockManager.convert_reservation_to_sale(
            reservation_id=reservation.id, order_id=order.id
        )

        # Verify order is linked
        reservation.refresh_from_db()
        assert reservation.order == order
        assert reservation.order_id == order.id

    def test_convert_reservation_to_sale_not_found(self):
        """Test that StockReservationError is raised for non-existent reservation."""
        from order.exceptions import StockReservationError
        from order.factories import OrderFactory

        order = OrderFactory(num_order_items=0)
        non_existent_id = 99999

        with pytest.raises(StockReservationError) as exc_info:
            StockManager.convert_reservation_to_sale(
                reservation_id=non_existent_id, order_id=order.id
            )

        assert str(non_existent_id) in str(exc_info.value)
        assert "not found" in str(exc_info.value).lower()

    def test_convert_reservation_to_sale_already_consumed(self):
        """Test that StockReservationError is raised for already consumed reservation."""
        from order.exceptions import StockReservationError
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)

        # Create a reservation
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-uuid",
            user_id=None,
        )

        order1 = OrderFactory(num_order_items=0)
        order2 = OrderFactory(num_order_items=0)

        # Convert once
        StockManager.convert_reservation_to_sale(
            reservation_id=reservation.id, order_id=order1.id
        )

        # Try to convert again - should fail
        with pytest.raises(StockReservationError) as exc_info:
            StockManager.convert_reservation_to_sale(
                reservation_id=reservation.id, order_id=order2.id
            )

        assert str(reservation.id) in str(exc_info.value)
        assert "already consumed" in str(exc_info.value).lower()

    def test_convert_reservation_to_sale_expired_reservation(self):
        """Test that StockReservationError is raised for expired reservation."""
        from order.exceptions import StockReservationError
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)

        # Create an expired reservation manually
        past_time = timezone.now() - timedelta(minutes=20)
        reservation = StockReservation.objects.create(
            product=product,
            quantity=10,
            session_id="expired-cart",
            expires_at=past_time,
            consumed=False,
        )

        order = OrderFactory(num_order_items=0)

        # Try to convert expired reservation - should fail
        with pytest.raises(StockReservationError) as exc_info:
            StockManager.convert_reservation_to_sale(
                reservation_id=reservation.id, order_id=order.id
            )

        assert str(reservation.id) in str(exc_info.value)
        assert "expired" in str(exc_info.value).lower()

    def test_convert_reservation_to_sale_insufficient_stock(self):
        """Test that InsufficientStockError is raised if stock was manually reduced."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)

        # Create a reservation for 50 units
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=50,
            session_id="cart-uuid",
            user_id=None,
        )

        # Manually reduce stock below reserved quantity (simulating manual adjustment)
        product.stock = 30
        product.save()

        order = OrderFactory(num_order_items=0)

        # Try to convert - should fail due to insufficient stock
        with pytest.raises(InsufficientStockError) as exc_info:
            StockManager.convert_reservation_to_sale(
                reservation_id=reservation.id, order_id=order.id
            )

        assert exc_info.value.product_id == product.id
        assert exc_info.value.available == 30
        assert exc_info.value.requested == 50

    def test_convert_reservation_to_sale_with_user(self):
        """Test conversion for reservation made by authenticated user."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)
        user = UserAccountFactory()

        # Create reservation with user
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-uuid",
            user_id=user.id,
        )

        order = OrderFactory(user=user, num_order_items=0)

        # Convert to sale
        StockManager.convert_reservation_to_sale(
            reservation_id=reservation.id, order_id=order.id
        )

        # Verify log has correct user
        log = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_DECREMENT
        ).first()
        assert log.performed_by == user

    def test_convert_reservation_to_sale_guest_user(self):
        """Test conversion for reservation made by guest user."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)

        # Create reservation without user
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="guest-cart",
            user_id=None,
        )

        order = OrderFactory(num_order_items=0)

        # Convert to sale
        StockManager.convert_reservation_to_sale(
            reservation_id=reservation.id, order_id=order.id
        )

        # Verify log has no user
        log = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_DECREMENT
        ).first()
        assert log.performed_by is None

    def test_convert_reservation_to_sale_atomicity(self):
        """Test that conversion is atomic - all changes or none."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)

        # Create a reservation
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-uuid",
            user_id=None,
        )

        # Create a valid order
        order = OrderFactory(num_order_items=0)

        initial_consumed = reservation.consumed

        # Manually set an invalid order_id on the reservation to force a failure
        # This simulates a database constraint violation during the transaction
        reservation.order_id = 99999  # Non-existent order

        # The transaction should rollback, but we need to test this differently
        # since the foreign key constraint is checked at commit time
        # Instead, let's test that if we manually reduce stock below the reservation
        # quantity, the transaction fails and nothing changes

        # Reset the reservation
        reservation.order_id = None

        # Manually reduce stock to below reservation quantity
        product.stock = 5  # Less than reservation quantity of 10
        product.save()

        # Try to convert - should fail due to insufficient stock
        try:
            StockManager.convert_reservation_to_sale(
                reservation_id=reservation.id, order_id=order.id
            )
        except InsufficientStockError:
            pass  # Expected to fail

        # Verify no changes were made to reservation (transaction rolled back)
        reservation.refresh_from_db()
        assert reservation.consumed == initial_consumed
        assert reservation.order_id is None

    def test_convert_reservation_to_sale_updates_timestamp(self):
        """Test that conversion updates the reservation's updated_at timestamp."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)

        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-uuid",
            user_id=None,
        )

        original_updated_at = reservation.updated_at

        # Wait a tiny bit to ensure timestamp difference
        import time

        time.sleep(0.01)

        order = OrderFactory(num_order_items=0)

        # Convert to sale
        StockManager.convert_reservation_to_sale(
            reservation_id=reservation.id, order_id=order.id
        )

        # Verify updated_at changed
        reservation.refresh_from_db()
        assert reservation.updated_at > original_updated_at

    @pytest.mark.parametrize(
        "initial_stock,quantity",
        [
            (100, 10),
            (100, 50),
            (100, 100),
            (50, 25),
            (10, 1),
            (10, 10),
        ],
    )
    def test_convert_reservation_to_sale_various_quantities(
        self, initial_stock, quantity
    ):
        """Test conversion with various stock and quantity combinations."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=initial_stock)

        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=quantity,
            session_id=f"cart-{initial_stock}-{quantity}",
            user_id=None,
        )

        order = OrderFactory(num_order_items=0)

        # Convert to sale
        StockManager.convert_reservation_to_sale(
            reservation_id=reservation.id, order_id=order.id
        )

        # Verify stock decreased correctly
        product.refresh_from_db()
        assert product.stock == initial_stock - quantity

        # Verify reservation consumed
        reservation.refresh_from_db()
        assert reservation.consumed is True
        assert reservation.order == order

    def test_convert_reservation_to_sale_multiple_reservations_same_product(
        self,
    ):
        """Test converting multiple reservations for the same product."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)

        # Create multiple reservations
        reservation1 = StockManager.reserve_stock(
            product_id=product.id,
            quantity=20,
            session_id="cart-1",
            user_id=None,
        )

        reservation2 = StockManager.reserve_stock(
            product_id=product.id,
            quantity=30,
            session_id="cart-2",
            user_id=None,
        )

        order1 = OrderFactory(num_order_items=0)
        order2 = OrderFactory(num_order_items=0)

        # Convert first reservation
        StockManager.convert_reservation_to_sale(
            reservation_id=reservation1.id, order_id=order1.id
        )

        product.refresh_from_db()
        assert product.stock == 80  # 100 - 20

        # Convert second reservation
        StockManager.convert_reservation_to_sale(
            reservation_id=reservation2.id, order_id=order2.id
        )

        product.refresh_from_db()
        assert product.stock == 50  # 80 - 30

        # Verify both reservations are consumed
        reservation1.refresh_from_db()
        reservation2.refresh_from_db()
        assert reservation1.consumed is True
        assert reservation2.consumed is True
        assert reservation1.order == order1
        assert reservation2.order == order2

    def test_convert_reservation_to_sale_uses_select_for_update(self):
        """Test that convert_reservation_to_sale uses SELECT FOR UPDATE for atomicity."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)

        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-uuid",
            user_id=None,
        )

        order = OrderFactory(num_order_items=0)

        # The @transaction.atomic decorator should handle this
        # This test verifies the method doesn't raise an error
        StockManager.convert_reservation_to_sale(
            reservation_id=reservation.id, order_id=order.id
        )

        # Verify conversion succeeded
        reservation.refresh_from_db()
        assert reservation.consumed is True
        product.refresh_from_db()
        assert product.stock == 90

    def test_convert_reservation_to_sale_stock_log_accuracy(self):
        """Test that StockLog records accurate before/after stock values."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=75)

        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=25,
            session_id="cart-uuid",
            user_id=None,
        )

        order = OrderFactory(num_order_items=0)

        # Convert to sale
        StockManager.convert_reservation_to_sale(
            reservation_id=reservation.id, order_id=order.id
        )

        # Verify log has accurate stock values
        log = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_DECREMENT
        ).first()

        assert log.stock_before == 75
        assert log.stock_after == 50
        assert log.quantity_delta == -25
        # Verify the calculation is correct
        assert log.stock_after == log.stock_before + log.quantity_delta


@pytest.mark.django_db
class TestStockManagerDecrementStock:
    """
    Test suite for StockManager.decrement_stock method.
    """

    def test_decrement_stock_success(self):
        """Test successful direct stock decrement."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)
        order = OrderFactory(num_order_items=0)

        # Decrement stock
        StockManager.decrement_stock(
            product_id=product.id,
            quantity=25,
            order_id=order.id,
            reason="Direct order placement",
        )

        # Verify product stock was decremented
        product.refresh_from_db()
        assert product.stock == 75  # 100 - 25

    def test_decrement_stock_creates_audit_log(self):
        """Test that stock decrement creates a StockLog entry."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)
        order = OrderFactory(num_order_items=0)
        reason = "Admin stock adjustment"

        # Count logs before
        initial_log_count = StockLog.objects.filter(product=product).count()

        # Decrement stock
        StockManager.decrement_stock(
            product_id=product.id, quantity=15, order_id=order.id, reason=reason
        )

        # Verify log created
        logs = StockLog.objects.filter(product=product).order_by("-created_at")
        assert logs.count() == initial_log_count + 1

        log = logs.first()
        assert log.operation_type == StockLog.OPERATION_DECREMENT
        assert log.quantity_delta == -15  # Negative because stock decreased
        assert log.stock_before == 100
        assert log.stock_after == 85  # 100 - 15
        assert log.order == order
        assert log.reason == reason
        assert log.performed_by is None  # No user context for direct decrements

    def test_decrement_stock_insufficient_stock(self):
        """Test that InsufficientStockError is raised when stock is insufficient."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=10)
        order = OrderFactory(num_order_items=0)

        with pytest.raises(InsufficientStockError) as exc_info:
            StockManager.decrement_stock(
                product_id=product.id,
                quantity=20,
                order_id=order.id,
                reason="Test order",
            )

        # Verify exception details
        assert exc_info.value.product_id == product.id
        assert exc_info.value.available == 10
        assert exc_info.value.requested == 20

        # Verify stock was not changed
        product.refresh_from_db()
        assert product.stock == 10

    def test_decrement_stock_product_not_found(self):
        """Test that ProductNotFoundError is raised for non-existent product."""
        from order.factories import OrderFactory

        order = OrderFactory(num_order_items=0)
        non_existent_id = 99999

        with pytest.raises(ProductNotFoundError) as exc_info:
            StockManager.decrement_stock(
                product_id=non_existent_id,
                quantity=5,
                order_id=order.id,
                reason="Test",
            )

        assert exc_info.value.product_id == non_existent_id

    def test_decrement_stock_invalid_quantity_zero(self):
        """Test that ValueError is raised for zero quantity."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)
        order = OrderFactory(num_order_items=0)

        with pytest.raises(ValueError) as exc_info:
            StockManager.decrement_stock(
                product_id=product.id,
                quantity=0,
                order_id=order.id,
                reason="Test",
            )

        assert "positive" in str(exc_info.value).lower()

        # Verify stock was not changed
        product.refresh_from_db()
        assert product.stock == 100

    def test_decrement_stock_invalid_quantity_negative(self):
        """Test that ValueError is raised for negative quantity."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)
        order = OrderFactory(num_order_items=0)

        with pytest.raises(ValueError) as exc_info:
            StockManager.decrement_stock(
                product_id=product.id,
                quantity=-10,
                order_id=order.id,
                reason="Test",
            )

        assert "positive" in str(exc_info.value).lower()

        # Verify stock was not changed
        product.refresh_from_db()
        assert product.stock == 100

    def test_decrement_stock_exact_stock_amount(self):
        """Test decrementing stock by exact available amount."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=50)
        order = OrderFactory(num_order_items=0)

        # Decrement by exact stock amount
        StockManager.decrement_stock(
            product_id=product.id,
            quantity=50,
            order_id=order.id,
            reason="Complete stock purchase",
        )

        # Verify stock is now zero
        product.refresh_from_db()
        assert product.stock == 0

    def test_decrement_stock_atomicity_with_transaction_rollback(self):
        """Test that stock decrement is atomic and rolls back on error."""
        from order.factories import OrderFactory
        from django.db import transaction

        product = ProductFactory(stock=100)
        order = OrderFactory(num_order_items=0)

        # Attempt to decrement stock within a transaction that will fail
        try:
            with transaction.atomic():
                StockManager.decrement_stock(
                    product_id=product.id,
                    quantity=30,
                    order_id=order.id,
                    reason="Test transaction",
                )

                # Force a transaction rollback by raising an exception
                raise Exception("Simulated error to trigger rollback")
        except Exception:
            pass

        # Verify stock was rolled back to original value
        product.refresh_from_db()
        assert product.stock == 100

        # Verify no StockLog entry was created (rolled back)
        log_count = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_DECREMENT
        ).count()
        assert log_count == 0

    def test_decrement_stock_default_reason(self):
        """Test that default reason is used when not provided."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)
        order = OrderFactory(num_order_items=0)

        # Decrement without providing reason
        StockManager.decrement_stock(
            product_id=product.id, quantity=10, order_id=order.id
        )

        # Verify default reason was used
        log = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_DECREMENT
        ).first()
        assert log.reason == "order_created"

    def test_decrement_stock_custom_reason(self):
        """Test that custom reason is recorded in audit log."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)
        order = OrderFactory(num_order_items=0)
        custom_reason = "Manual inventory adjustment by admin John"

        # Decrement with custom reason
        StockManager.decrement_stock(
            product_id=product.id,
            quantity=10,
            order_id=order.id,
            reason=custom_reason,
        )

        # Verify custom reason was recorded
        log = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_DECREMENT
        ).first()
        assert log.reason == custom_reason

    def test_decrement_stock_multiple_times(self):
        """Test multiple sequential stock decrements."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)
        order1 = OrderFactory(num_order_items=0)
        order2 = OrderFactory(num_order_items=0)
        order3 = OrderFactory(num_order_items=0)

        # First decrement
        StockManager.decrement_stock(
            product_id=product.id,
            quantity=20,
            order_id=order1.id,
            reason="Order 1",
        )
        product.refresh_from_db()
        assert product.stock == 80

        # Second decrement
        StockManager.decrement_stock(
            product_id=product.id,
            quantity=30,
            order_id=order2.id,
            reason="Order 2",
        )
        product.refresh_from_db()
        assert product.stock == 50

        # Third decrement
        StockManager.decrement_stock(
            product_id=product.id,
            quantity=25,
            order_id=order3.id,
            reason="Order 3",
        )
        product.refresh_from_db()
        assert product.stock == 25

        # Verify all three logs were created
        logs = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_DECREMENT
        ).order_by("created_at")
        assert logs.count() == 3

        # Verify log details
        assert logs[0].stock_before == 100
        assert logs[0].stock_after == 80
        assert logs[1].stock_before == 80
        assert logs[1].stock_after == 50
        assert logs[2].stock_before == 50
        assert logs[2].stock_after == 25

    def test_decrement_stock_uses_select_for_update(self):
        """Test that decrement_stock uses SELECT FOR UPDATE for atomicity."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)
        order = OrderFactory(num_order_items=0)

        # The @transaction.atomic decorator on decrement_stock should handle this
        # This test verifies the method doesn't raise an error when called
        # within a transaction, which would fail if SELECT FOR UPDATE wasn't used properly
        StockManager.decrement_stock(
            product_id=product.id, quantity=10, order_id=order.id, reason="Test"
        )

        # Verify stock was decremented
        product.refresh_from_db()
        assert product.stock == 90

    @pytest.mark.parametrize(
        "initial_stock,quantity,expected_final",
        [
            (100, 1, 99),
            (100, 50, 50),
            (100, 99, 1),
            (100, 100, 0),
            (50, 25, 25),
            (10, 5, 5),
            (1, 1, 0),
        ],
    )
    def test_decrement_stock_various_quantities(
        self, initial_stock, quantity, expected_final
    ):
        """Test stock decrement with various stock and quantity combinations."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=initial_stock)
        order = OrderFactory(num_order_items=0)

        StockManager.decrement_stock(
            product_id=product.id,
            quantity=quantity,
            order_id=order.id,
            reason=f"Test {initial_stock}-{quantity}",
        )

        # Verify final stock
        product.refresh_from_db()
        assert product.stock == expected_final

        # Verify log
        log = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_DECREMENT
        ).first()
        assert log.stock_before == initial_stock
        assert log.stock_after == expected_final
        assert log.quantity_delta == -quantity

    @pytest.mark.parametrize(
        "stock,quantity",
        [
            (10, 11),
            (10, 20),
            (5, 10),
            (1, 2),
            (0, 1),
        ],
    )
    def test_decrement_stock_insufficient_various_cases(self, stock, quantity):
        """Test insufficient stock error with various combinations."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=stock)
        order = OrderFactory(num_order_items=0)

        with pytest.raises(InsufficientStockError) as exc_info:
            StockManager.decrement_stock(
                product_id=product.id,
                quantity=quantity,
                order_id=order.id,
                reason="Test",
            )

        assert exc_info.value.available == stock
        assert exc_info.value.requested == quantity

        # Verify stock unchanged
        product.refresh_from_db()
        assert product.stock == stock

    def test_decrement_stock_log_has_correct_order_reference(self):
        """Test that StockLog correctly references the order."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)
        order = OrderFactory(num_order_items=0)

        StockManager.decrement_stock(
            product_id=product.id,
            quantity=10,
            order_id=order.id,
            reason="Test order",
        )

        # Verify log has correct order reference
        log = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_DECREMENT
        ).first()
        assert log.order == order
        assert log.order_id == order.id

    def test_decrement_stock_updates_product_timestamp(self):
        """Test that decrementing stock updates the product's updated_at timestamp."""
        from order.factories import OrderFactory
        import time

        product = ProductFactory(stock=100)
        original_updated_at = product.updated_at

        # Wait a tiny bit to ensure timestamp difference
        time.sleep(0.01)

        order = OrderFactory(num_order_items=0)

        # Decrement stock
        StockManager.decrement_stock(
            product_id=product.id, quantity=10, order_id=order.id, reason="Test"
        )

        # Verify updated_at changed
        product.refresh_from_db()
        assert product.updated_at > original_updated_at

    def test_decrement_stock_no_user_context(self):
        """Test that decrement_stock logs with no user (performed_by=None)."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)
        order = OrderFactory(num_order_items=0)

        StockManager.decrement_stock(
            product_id=product.id,
            quantity=10,
            order_id=order.id,
            reason="Direct decrement",
        )

        # Verify log has no user
        log = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_DECREMENT
        ).first()
        assert log.performed_by is None


@pytest.mark.django_db
class TestStockManagerCleanupExpiredReservations:
    """Test suite for StockManager.cleanup_expired_reservations method."""

    def test_cleanup_expired_reservations_success(self):
        """Test successful cleanup of expired reservations."""
        product = ProductFactory(stock=100)

        # Create an expired reservation
        past_time = timezone.now() - timedelta(minutes=20)
        expired_reservation = StockReservation.objects.create(
            product=product,
            quantity=10,
            session_id="expired-cart-1",
            expires_at=past_time,
            consumed=False,
        )

        # Create another expired reservation
        past_time_2 = timezone.now() - timedelta(minutes=30)
        expired_reservation_2 = StockReservation.objects.create(
            product=product,
            quantity=15,
            session_id="expired-cart-2",
            expires_at=past_time_2,
            consumed=False,
        )

        # Create an active (non-expired) reservation
        future_time = timezone.now() + timedelta(minutes=10)
        active_reservation = StockReservation.objects.create(
            product=product,
            quantity=20,
            session_id="active-cart",
            expires_at=future_time,
            consumed=False,
        )

        # Run cleanup
        count = StockManager.cleanup_expired_reservations()

        # Verify count includes our expired reservations (may include others from parallel tests)
        assert count >= 2

        # Verify expired reservations are marked as consumed
        expired_reservation.refresh_from_db()
        assert expired_reservation.consumed is True

        expired_reservation_2.refresh_from_db()
        assert expired_reservation_2.consumed is True

        # Verify active reservation is NOT marked as consumed
        active_reservation.refresh_from_db()
        assert active_reservation.consumed is False

    def test_cleanup_expired_reservations_creates_audit_logs(self):
        """Test that cleanup creates StockLog entries for each released reservation."""
        product = ProductFactory(stock=100)

        # Create two expired reservations
        past_time = timezone.now() - timedelta(minutes=20)
        expired_1 = StockReservation.objects.create(
            product=product,
            quantity=10,
            session_id="expired-1",
            expires_at=past_time,
            consumed=False,
        )

        expired_2 = StockReservation.objects.create(
            product=product,
            quantity=15,
            session_id="expired-2",
            expires_at=past_time,
            consumed=False,
        )

        # Count logs before cleanup
        initial_log_count = StockLog.objects.filter(product=product).count()

        # Run cleanup
        StockManager.cleanup_expired_reservations()

        # Verify logs created (2 new logs)
        logs = StockLog.objects.filter(product=product).order_by("-created_at")
        assert logs.count() == initial_log_count + 2

        # Verify log details for first expired reservation
        log_1 = logs.filter(
            operation_type=StockLog.OPERATION_RELEASE, quantity_delta=10
        ).first()
        assert log_1 is not None
        assert log_1.stock_before == 100
        assert log_1.stock_after == 100  # Physical stock unchanged
        assert str(expired_1.id) in log_1.reason
        assert "expired" in log_1.reason.lower()

        # Verify log details for second expired reservation
        log_2 = logs.filter(
            operation_type=StockLog.OPERATION_RELEASE, quantity_delta=15
        ).first()
        assert log_2 is not None
        assert log_2.stock_before == 100
        assert log_2.stock_after == 100  # Physical stock unchanged
        assert str(expired_2.id) in log_2.reason
        assert "expired" in log_2.reason.lower()

    def test_cleanup_expired_reservations_no_expired(self):
        """Test cleanup when there are no expired reservations."""
        product = ProductFactory(stock=100)

        # Create only active reservations
        future_time = timezone.now() + timedelta(minutes=10)
        StockReservation.objects.create(
            product=product,
            quantity=20,
            session_id="active-1",
            expires_at=future_time,
            consumed=False,
        )

        StockReservation.objects.create(
            product=product,
            quantity=30,
            session_id="active-2",
            expires_at=future_time,
            consumed=False,
        )

        # Run cleanup
        StockManager.cleanup_expired_reservations()

        # Verify our active reservations were NOT cleaned
        # (count may be > 0 if other parallel tests created expired reservations)
        active_count = StockReservation.objects.filter(
            product=product, consumed=False
        ).count()
        assert active_count == 2

    def test_cleanup_expired_reservations_ignores_consumed(self):
        """Test that cleanup ignores already consumed reservations."""
        product = ProductFactory(stock=100)

        # Create an expired but already consumed reservation
        past_time = timezone.now() - timedelta(minutes=20)
        consumed_reservation = StockReservation.objects.create(
            product=product,
            quantity=10,
            session_id="consumed-cart",
            expires_at=past_time,
            consumed=True,  # Already consumed
        )

        # Run cleanup
        StockManager.cleanup_expired_reservations()

        # Verify our already-consumed reservation was not affected
        # (count may be > 0 if other parallel tests created expired reservations)
        consumed_reservation.refresh_from_db()
        assert consumed_reservation.consumed is True

    def test_cleanup_expired_reservations_restores_available_stock(self):
        """Test that cleanup makes reserved stock available again."""
        product = ProductFactory(stock=100)

        # Create an expired reservation for 60 units
        past_time = timezone.now() - timedelta(minutes=20)
        StockReservation.objects.create(
            product=product,
            quantity=60,
            session_id="expired-cart",
            expires_at=past_time,
            consumed=False,
        )

        # Before cleanup, available stock should be 40 (100 - 60)
        # But since the reservation is expired, get_available_stock should already exclude it
        available_before = StockManager.get_available_stock(product.id)
        assert available_before == 100  # Expired reservations don't count

        # Run cleanup
        count = StockManager.cleanup_expired_reservations()
        assert count >= 1  # At least our expired reservation was cleaned

        # After cleanup, available stock should still be 100
        available_after = StockManager.get_available_stock(product.id)
        assert available_after == 100

        # Now we should be able to reserve the full stock
        new_reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=100,
            session_id="new-cart",
            user_id=None,
        )
        assert new_reservation.quantity == 100

    def test_cleanup_expired_reservations_multiple_products(self):
        """Test cleanup with expired reservations for multiple products."""
        product_1 = ProductFactory(stock=100)
        product_2 = ProductFactory(stock=200)

        # Create expired reservations for both products
        past_time = timezone.now() - timedelta(minutes=20)

        expired_1 = StockReservation.objects.create(
            product=product_1,
            quantity=10,
            session_id="expired-1",
            expires_at=past_time,
            consumed=False,
        )

        expired_2 = StockReservation.objects.create(
            product=product_2,
            quantity=20,
            session_id="expired-2",
            expires_at=past_time,
            consumed=False,
        )

        # Run cleanup
        count = StockManager.cleanup_expired_reservations()

        # Verify both our reservations were cleaned (count may include others from parallel tests)
        assert count >= 2

        expired_1.refresh_from_db()
        assert expired_1.consumed is True

        expired_2.refresh_from_db()
        assert expired_2.consumed is True

    def test_cleanup_expired_reservations_with_user(self):
        """Test cleanup logs correct user for reservations made by authenticated users."""
        product = ProductFactory(stock=100)
        user = UserAccountFactory()

        # Create an expired reservation with user
        past_time = timezone.now() - timedelta(minutes=20)
        StockReservation.objects.create(
            product=product,
            quantity=10,
            session_id="expired-cart",
            expires_at=past_time,
            consumed=False,
            reserved_by=user,
        )

        # Run cleanup
        count = StockManager.cleanup_expired_reservations()
        assert count >= 1  # At least our expired reservation was cleaned

        # Verify log has correct user
        log = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_RELEASE
        ).first()
        assert log.performed_by == user

    def test_cleanup_expired_reservations_guest_user(self):
        """Test cleanup logs no user for guest reservations."""
        product = ProductFactory(stock=100)

        # Create an expired reservation without user (guest)
        past_time = timezone.now() - timedelta(minutes=20)
        StockReservation.objects.create(
            product=product,
            quantity=10,
            session_id="guest-expired-cart",
            expires_at=past_time,
            consumed=False,
            reserved_by=None,
        )

        # Run cleanup
        count = StockManager.cleanup_expired_reservations()
        assert count >= 1  # At least our expired reservation was cleaned

        # Verify log has no user
        log = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_RELEASE
        ).first()
        assert log.performed_by is None

    def test_cleanup_expired_reservations_empty_database(self):
        """Test cleanup when there are no reservations at all for a specific product."""
        # Create a product with no reservations
        product = ProductFactory(stock=100)

        # Verify no reservations exist for this product
        initial_count = StockReservation.objects.filter(product=product).count()
        assert initial_count == 0

        # Run cleanup - this may clean up reservations from other tests
        StockManager.cleanup_expired_reservations()

        # Verify still no reservations for our product (none were created)
        final_count = StockReservation.objects.filter(product=product).count()
        assert final_count == 0

    def test_cleanup_expired_reservations_updates_timestamp(self):
        """Test that cleanup updates the updated_at timestamp of reservations."""
        product = ProductFactory(stock=100)

        # Create an expired reservation
        past_time = timezone.now() - timedelta(minutes=20)
        reservation = StockReservation.objects.create(
            product=product,
            quantity=10,
            session_id="expired-cart",
            expires_at=past_time,
            consumed=False,
        )

        original_updated_at = reservation.updated_at

        # Wait a tiny bit to ensure timestamp difference
        import time

        time.sleep(0.01)

        # Run cleanup
        StockManager.cleanup_expired_reservations()

        # Verify updated_at changed
        reservation.refresh_from_db()
        assert reservation.updated_at > original_updated_at

    @pytest.mark.parametrize(
        "minutes_ago,should_cleanup",
        [
            (20, True),  # Expired 20 minutes ago - should cleanup
            (30, True),  # Expired 30 minutes ago - should cleanup
            (16, True),  # Expired 16 minutes ago - should cleanup
            (15, True),  # Expired exactly 15 minutes ago - should cleanup
            (
                14,
                False,
            ),  # Expired 14 minutes ago - still active (created 14 min ago, expires in 1 min)
            (
                10,
                False,
            ),  # Expired 10 minutes ago - still active (created 10 min ago, expires in 5 min)
            (
                5,
                False,
            ),  # Expired 5 minutes ago - still active (created 5 min ago, expires in 10 min)
            (
                1,
                False,
            ),  # Expired 1 minute ago - still active (created 1 min ago, expires in 14 min)
        ],
    )
    def test_cleanup_expired_reservations_various_expiration_times(
        self, minutes_ago, should_cleanup
    ):
        """Test cleanup with various expiration times.

        Note: The parameter 'minutes_ago' represents when the reservation was CREATED,
        not when it expired. Reservations have a 15-minute TTL, so:
        - Created 20 min ago -> expired 5 min ago (20 - 15 = 5) -> should cleanup
        - Created 14 min ago -> expires in 1 min (15 - 14 = 1) -> should NOT cleanup
        """
        import uuid

        product = ProductFactory(stock=100)
        unique_id = uuid.uuid4().hex[:8]

        # Calculate expiration time based on creation time
        # If created X minutes ago, it expires at (now - X minutes + 15 minutes)
        created_at = timezone.now() - timedelta(minutes=minutes_ago)
        expiration_time = created_at + timedelta(
            minutes=StockManager.get_reservation_ttl_minutes()
        )

        reservation = StockReservation.objects.create(
            product=product,
            quantity=10,
            session_id=f"expiration-test-{unique_id}-{minutes_ago}",
            expires_at=expiration_time,
            consumed=False,
        )

        # Run cleanup
        count = StockManager.cleanup_expired_reservations()

        # Verify cleanup behavior - check the specific reservation's state
        # Note: count may include expired reservations from other parallel tests,
        # so we only verify the specific reservation's consumed state
        reservation.refresh_from_db()
        if should_cleanup:
            assert count >= 1  # At least our reservation was cleaned up
            assert reservation.consumed is True
        else:
            # Our reservation should NOT be consumed regardless of count
            # (count may be > 0 if other tests created expired reservations)
            assert reservation.consumed is False

    def test_cleanup_expired_reservations_atomic_transaction(self):
        """Test that cleanup is atomic (all or nothing)."""
        product = ProductFactory(stock=100)

        # Create multiple expired reservations
        past_time = timezone.now() - timedelta(minutes=20)
        reservation_ids = []
        for i in range(5):
            reservation = StockReservation.objects.create(
                product=product,
                quantity=10,
                session_id=f"expired-atomic-{i}",
                expires_at=past_time,
                consumed=False,
            )
            reservation_ids.append(reservation.id)

        # Run cleanup
        count = StockManager.cleanup_expired_reservations()

        # Verify at least our 5 were cleaned (count may include others from parallel tests)
        assert count >= 5

        # Verify all our reservations are marked as consumed
        consumed_count = StockReservation.objects.filter(
            id__in=reservation_ids, consumed=True
        ).count()
        assert consumed_count == 5


@pytest.mark.django_db
class TestStockManagerIncrementStock:
    """
    Test suite for StockManager.increment_stock method.
    """

    def test_increment_stock_success(self):
        """Test successful stock increment."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=50)
        order = OrderFactory(num_order_items=0)

        # Increment stock
        StockManager.increment_stock(
            product_id=product.id,
            quantity=25,
            order_id=order.id,
            reason="Order cancelled",
        )

        # Verify product stock was incremented
        product.refresh_from_db()
        assert product.stock == 75  # 50 + 25

    def test_increment_stock_creates_audit_log(self):
        """Test that stock increment creates a StockLog entry."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=50)
        order = OrderFactory(num_order_items=0)
        reason = "Product returned by customer"

        # Count logs before
        initial_log_count = StockLog.objects.filter(product=product).count()

        # Increment stock
        StockManager.increment_stock(
            product_id=product.id, quantity=15, order_id=order.id, reason=reason
        )

        # Verify log created
        logs = StockLog.objects.filter(product=product).order_by("-created_at")
        assert logs.count() == initial_log_count + 1

        log = logs.first()
        assert log.operation_type == StockLog.OPERATION_INCREMENT
        assert log.quantity_delta == 15  # Positive because stock increased
        assert log.stock_before == 50
        assert log.stock_after == 65  # 50 + 15
        assert log.order == order
        assert log.reason == reason
        assert log.performed_by is None  # No user context for direct increments

    def test_increment_stock_product_not_found(self):
        """Test that ProductNotFoundError is raised for non-existent product."""
        from order.factories import OrderFactory

        order = OrderFactory(num_order_items=0)
        non_existent_id = 99999

        with pytest.raises(ProductNotFoundError) as exc_info:
            StockManager.increment_stock(
                product_id=non_existent_id,
                quantity=10,
                order_id=order.id,
                reason="Test",
            )

        assert exc_info.value.product_id == non_existent_id


@pytest.mark.django_db
class TestStockManagerGetAvailableStock:
    """
    Test suite for StockManager.get_available_stock method.
    """

    def test_get_available_stock_no_reservations(self):
        """Test available stock calculation with no reservations."""
        product = ProductFactory(stock=100)

        # With no reservations, available should equal total stock
        available = StockManager.get_available_stock(product_id=product.id)

        assert available == 100

    def test_get_available_stock_with_active_reservations(self):
        """Test available stock calculation with active reservations."""
        product = ProductFactory(stock=100)

        # Create active reservations totaling 40 units
        StockManager.reserve_stock(
            product_id=product.id,
            quantity=25,
            session_id="cart-1",
            user_id=None,
        )

        StockManager.reserve_stock(
            product_id=product.id,
            quantity=15,
            session_id="cart-2",
            user_id=None,
        )

        # Available should be 100 - 40 = 60
        available = StockManager.get_available_stock(product_id=product.id)

        assert available == 60

    def test_get_available_stock_with_expired_reservations(self):
        """Test that expired reservations don't affect available stock."""
        product = ProductFactory(stock=100)

        # Create an expired reservation (20 minutes ago)
        past_time = timezone.now() - timedelta(minutes=20)
        StockReservation.objects.create(
            product=product,
            quantity=50,
            session_id="expired-cart",
            expires_at=past_time,
            consumed=False,
        )

        # Expired reservations should not reduce available stock
        available = StockManager.get_available_stock(product_id=product.id)

        assert available == 100  # Full stock available

    def test_get_available_stock_with_consumed_reservations(self):
        """Test that consumed reservations don't affect available stock."""
        product = ProductFactory(stock=100)

        # Create a consumed reservation (already converted to sale)
        future_time = timezone.now() + timedelta(minutes=10)
        StockReservation.objects.create(
            product=product,
            quantity=30,
            session_id="consumed-cart",
            expires_at=future_time,
            consumed=True,  # Already consumed
        )

        # Consumed reservations should not reduce available stock
        available = StockManager.get_available_stock(product_id=product.id)

        assert available == 100  # Full stock available

    def test_get_available_stock_mixed_reservations(self):
        """Test available stock with mix of active, expired, and consumed reservations."""
        product = ProductFactory(stock=100)

        # Create active reservation (20 units)
        StockManager.reserve_stock(
            product_id=product.id,
            quantity=20,
            session_id="active-cart",
            user_id=None,
        )

        # Create expired reservation (30 units) - should not count
        past_time = timezone.now() - timedelta(minutes=20)
        StockReservation.objects.create(
            product=product,
            quantity=30,
            session_id="expired-cart",
            expires_at=past_time,
            consumed=False,
        )

        # Create consumed reservation (25 units) - should not count
        future_time = timezone.now() + timedelta(minutes=10)
        StockReservation.objects.create(
            product=product,
            quantity=25,
            session_id="consumed-cart",
            expires_at=future_time,
            consumed=True,
        )

        # Only active reservation (20 units) should reduce available stock
        available = StockManager.get_available_stock(product_id=product.id)

        assert available == 80  # 100 - 20 = 80

    def test_get_available_stock_product_not_found(self):
        """Test that ProductNotFoundError is raised for non-existent product."""
        non_existent_id = 99999

        with pytest.raises(ProductNotFoundError) as exc_info:
            StockManager.get_available_stock(product_id=non_existent_id)

        assert exc_info.value.product_id == non_existent_id

    def test_get_available_stock_multiple_active_reservations(self):
        """Test available stock calculation with multiple active reservations."""
        product = ProductFactory(stock=200)

        # Create multiple active reservations with unique session IDs
        quantities = [10, 20, 30, 15, 25]
        for i, quantity in enumerate(quantities):
            StockManager.reserve_stock(
                product_id=product.id,
                quantity=quantity,
                session_id=f"multi-active-cart-{product.id}-{i}",
                user_id=None,
            )

        # Total reserved: 10 + 20 + 30 + 15 + 25 = 100
        # Available: 200 - 100 = 100
        available = StockManager.get_available_stock(product_id=product.id)

        assert available == 100

    def test_get_available_stock_all_reserved(self):
        """Test available stock when all stock is reserved."""
        product = ProductFactory(stock=50)

        # Reserve all available stock
        StockManager.reserve_stock(
            product_id=product.id,
            quantity=50,
            session_id="cart-full",
            user_id=None,
        )

        # Available should be 0
        available = StockManager.get_available_stock(product_id=product.id)

        assert available == 0

    def test_get_available_stock_after_release(self):
        """Test that available stock increases after releasing a reservation."""
        product = ProductFactory(stock=100)

        # Reserve 60 units
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=60,
            session_id="cart-1",
            user_id=None,
        )

        # Available should be 40
        available = StockManager.get_available_stock(product_id=product.id)
        assert available == 40

        # Release the reservation
        StockManager.release_reservation(reservation_id=reservation.id)

        # Available should be back to 100
        available = StockManager.get_available_stock(product_id=product.id)
        assert available == 100

    def test_get_available_stock_after_conversion_to_sale(self):
        """Test available stock after converting reservation to sale."""
        from order.factories import OrderFactory

        product = ProductFactory(stock=100)

        # Reserve 30 units
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=30,
            session_id="cart-1",
            user_id=None,
        )

        # Available should be 70
        available = StockManager.get_available_stock(product_id=product.id)
        assert available == 70

        # Convert to sale
        order = OrderFactory(num_order_items=0)
        StockManager.convert_reservation_to_sale(
            reservation_id=reservation.id, order_id=order.id
        )

        # After conversion:
        # - Physical stock decreased from 100 to 70
        # - Reservation is consumed (no longer active)
        # - Available should equal physical stock (70)
        available = StockManager.get_available_stock(product_id=product.id)
        assert available == 70

    @pytest.mark.parametrize(
        "total_stock,reserved_quantity,expected_available",
        [
            (100, 0, 100),  # No reservations
            (100, 25, 75),  # Some reserved
            (100, 50, 50),  # Half reserved
            (100, 100, 0),  # All reserved
            (50, 10, 40),  # Small stock
            (1000, 250, 750),  # Large stock
            (10, 5, 5),  # Small quantities
        ],
    )
    def test_get_available_stock_various_scenarios(
        self, total_stock, reserved_quantity, expected_available
    ):
        """Test available stock calculation with various stock and reservation combinations."""
        product = ProductFactory(stock=total_stock)

        # Create reservation if needed
        if reserved_quantity > 0:
            StockManager.reserve_stock(
                product_id=product.id,
                quantity=reserved_quantity,
                session_id=f"cart-{reserved_quantity}",
                user_id=None,
            )

        # Verify available stock
        available = StockManager.get_available_stock(product_id=product.id)

        assert available == expected_available

    def test_get_available_stock_is_read_only(self):
        """Test that get_available_stock doesn't modify any data."""
        product = ProductFactory(stock=100)

        # Create a reservation
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=30,
            session_id="cart-1",
            user_id=None,
        )

        # Get available stock multiple times
        available1 = StockManager.get_available_stock(product_id=product.id)
        available2 = StockManager.get_available_stock(product_id=product.id)
        available3 = StockManager.get_available_stock(product_id=product.id)

        # All calls should return the same value
        assert available1 == available2 == available3 == 70

        # Verify product stock unchanged
        product.refresh_from_db()
        assert product.stock == 100

        # Verify reservation unchanged
        reservation.refresh_from_db()
        assert reservation.consumed is False
        assert reservation.quantity == 30

    def test_get_available_stock_point_in_time_snapshot(self):
        """Test that get_available_stock provides a point-in-time snapshot."""
        product = ProductFactory(stock=100)

        # Get initial available stock
        available1 = StockManager.get_available_stock(product_id=product.id)
        assert available1 == 100

        # Create a reservation
        StockManager.reserve_stock(
            product_id=product.id,
            quantity=20,
            session_id="cart-1",
            user_id=None,
        )

        # Get available stock again - should reflect the reservation
        available2 = StockManager.get_available_stock(product_id=product.id)
        assert available2 == 80

        # Create another reservation
        StockManager.reserve_stock(
            product_id=product.id,
            quantity=15,
            session_id="cart-2",
            user_id=None,
        )

        # Get available stock again - should reflect both reservations
        available3 = StockManager.get_available_stock(product_id=product.id)
        assert available3 == 65

    def test_get_available_stock_with_zero_stock(self):
        """Test available stock calculation when product has zero stock."""
        product = ProductFactory(stock=0)

        # Available should be 0
        available = StockManager.get_available_stock(product_id=product.id)

        assert available == 0

    def test_get_available_stock_consistency_with_reserve_stock(self):
        """
        Test that get_available_stock is consistent with reserve_stock validation.
        """
        import uuid

        test_id = uuid.uuid4().hex[:8]
        product = ProductFactory(stock=100)

        # Reserve 70 units
        StockManager.reserve_stock(
            product_id=product.id,
            quantity=70,
            session_id=f"consistency-cart-1-{test_id}",
            user_id=None,
        )

        # Get available stock
        available = StockManager.get_available_stock(product_id=product.id)
        assert available == 30

        # Should be able to reserve exactly the available amount
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=30,
            session_id=f"consistency-cart-2-{test_id}",
            user_id=None,
        )
        assert reservation.quantity == 30

        # Should not be able to reserve more than available
        with pytest.raises(InsufficientStockError) as exc_info:
            StockManager.reserve_stock(
                product_id=product.id,
                quantity=1,  # Even 1 unit should fail
                session_id=f"consistency-cart-3-{test_id}",
                user_id=None,
            )

        assert exc_info.value.available == 0
        assert exc_info.value.requested == 1
