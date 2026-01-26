import pytest
from datetime import timedelta
from django.utils import timezone

from order.stock import StockManager
from order.models import StockReservation, StockLog
from product.factories import ProductFactory


@pytest.mark.django_db
class TestExpiredReservationsAreReleased:
    """
    Expired Reservations Are Released.

    This test suite validates that the cleanup_expired_reservations method
    correctly identifies and releases expired reservations while leaving
    active reservations untouched.
    """

    @pytest.mark.parametrize(
        "minutes_expired,quantity,description",
        [
            # Recently expired
            (1, 10, "Expired 1 minute ago"),
            (5, 25, "Expired 5 minutes ago"),
            # Moderately expired
            (15, 50, "Expired 15 minutes ago"),
            (30, 75, "Expired 30 minutes ago"),
            (60, 100, "Expired 1 hour ago"),
            # Long expired
            (120, 10, "Expired 2 hours ago"),
            (1440, 50, "Expired 1 day ago"),
            # Edge cases
            (0, 5, "Just expired (0 minutes)"),
            (10080, 1, "Expired 1 week ago"),
        ],
    )
    def test_cleanup_releases_expired_reservations(
        self, minutes_expired, quantity, description
    ):
        """
        Test that cleanup releases reservations that have expired.

        This test verifies that any reservation with expires_at < current_time
        and consumed = False is marked as consumed (released) by the cleanup
        process.

        Args:
            minutes_expired: How many minutes ago the reservation expired
            quantity: The quantity reserved
            description: Human-readable description of the test case

        Test Requirements:
        - Create reservations with various expiration times (past, future, now)
        - Verify: Expired reservations released
        - Verify: Active reservations not released
        """
        # Setup: Create product with sufficient stock
        product = ProductFactory(stock=200)

        # Create an expired reservation
        # Calculate when it was created and when it expired
        expired_at = timezone.now() - timedelta(minutes=minutes_expired)
        created_at = expired_at - timedelta(
            minutes=StockManager.RESERVATION_TTL_MINUTES
        )

        expired_reservation = StockReservation.objects.create(
            product=product,
            quantity=quantity,
            session_id=f"expired-{minutes_expired}-{quantity}",
            expires_at=expired_at,
            consumed=False,
            created_at=created_at,
            updated_at=created_at,
        )

        # Verify initial state
        assert expired_reservation.consumed is False, (
            f"Reservation should start as not consumed for {description}"
        )

        # Run cleanup
        count = StockManager.cleanup_expired_reservations()

        # Verify: Cleanup found and released the expired reservation
        assert count == 1, (
            f"Cleanup should have released 1 reservation for {description}, "
            f"but released {count}"
        )

        # Refresh from database
        expired_reservation.refresh_from_db()

        # Verify: Reservation is now marked as consumed (released)
        assert expired_reservation.consumed is True, (
            f"Expired reservation should be marked as consumed for {description}"
        )

        # Verify: StockLog entry was created for the release
        stock_logs = StockLog.objects.filter(
            product=product, operation_type="RELEASE"
        )
        assert stock_logs.count() == 1, (
            f"Should have 1 RELEASE log entry for {description}"
        )

        log = stock_logs.first()
        assert log.quantity_delta == quantity, (
            f"Log should record quantity {quantity} for {description}"
        )
        assert (
            "expired" in log.reason.lower() or "cleanup" in log.reason.lower()
        ), f"Log reason should mention expiration or cleanup for {description}"

    @pytest.mark.parametrize(
        "minutes_until_expiry,quantity,description",
        [
            # Will expire soon but not yet
            (1, 10, "Expires in 1 minute"),
            (5, 25, "Expires in 5 minutes"),
            (10, 50, "Expires in 10 minutes"),
            (14, 75, "Expires in 14 minutes"),
            # Just created (full TTL remaining)
            (15, 100, "Just created (15 minutes remaining)"),
            # Future expiration
            (30, 10, "Expires in 30 minutes"),
            (60, 50, "Expires in 1 hour"),
            (1440, 5, "Expires in 1 day"),
        ],
    )
    def test_cleanup_does_not_release_active_reservations(
        self, minutes_until_expiry, quantity, description
    ):
        """
        Test that cleanup does NOT release reservations that are still active.

        This test verifies that reservations with expires_at > current_time
        are left untouched by the cleanup process.

        Args:
            minutes_until_expiry: How many minutes until the reservation expires
            quantity: The quantity reserved
            description: Human-readable description of the test case
        """
        # Setup: Create product with sufficient stock
        product = ProductFactory(stock=200)

        # Create an active reservation (expires in the future)
        expires_at = timezone.now() + timedelta(minutes=minutes_until_expiry)
        created_at = expires_at - timedelta(
            minutes=StockManager.RESERVATION_TTL_MINUTES
        )

        active_reservation = StockReservation.objects.create(
            product=product,
            quantity=quantity,
            session_id=f"active-{minutes_until_expiry}-{quantity}",
            expires_at=expires_at,
            consumed=False,
            created_at=created_at,
            updated_at=created_at,
        )

        # Verify initial state
        assert active_reservation.consumed is False, (
            f"Reservation should start as not consumed for {description}"
        )

        # Run cleanup
        count = StockManager.cleanup_expired_reservations()

        # Verify: Cleanup did NOT release the active reservation
        assert count == 0, (
            f"Cleanup should not have released any reservations for {description}, "
            f"but released {count}"
        )

        # Refresh from database
        active_reservation.refresh_from_db()

        # Verify: Reservation is still not consumed
        assert active_reservation.consumed is False, (
            f"Active reservation should remain not consumed for {description}"
        )

        # Verify: No RELEASE log entries were created
        release_logs = StockLog.objects.filter(
            product=product, operation_type="RELEASE"
        )
        assert release_logs.count() == 0, (
            f"Should have no RELEASE log entries for {description}"
        )

    @pytest.mark.parametrize(
        "expired_count,active_count,expired_quantities,active_quantities",
        [
            # Mix of expired and active
            (1, 1, [10], [20]),
            (2, 1, [10, 15], [25]),
            (1, 2, [30], [10, 20]),
            (3, 2, [5, 10, 15], [20, 25]),
            # More complex scenarios
            (5, 3, [10, 20, 30, 40, 50], [15, 25, 35]),
            (2, 5, [100, 50], [10, 20, 30, 40, 50]),
            # Edge cases
            (10, 0, [5] * 10, []),  # All expired
            (0, 10, [], [5] * 10),  # All active
            (1, 10, [100], [10] * 10),  # One expired, many active
            (10, 1, [10] * 10, [100]),  # Many expired, one active
        ],
    )
    def test_cleanup_handles_mixed_reservations(
        self, expired_count, active_count, expired_quantities, active_quantities
    ):
        """
        Test that cleanup correctly handles a mix of expired and active reservations.

        This test verifies that when there are both expired and active reservations,
        cleanup only releases the expired ones and leaves active ones untouched.

        Args:
            expired_count: Number of expired reservations to create
            active_count: Number of active reservations to create
            expired_quantities: List of quantities for expired reservations
            active_quantities: List of quantities for active reservations
        """
        # Setup: Create product with sufficient stock
        product = ProductFactory(stock=1000)

        # Create expired reservations
        expired_reservations = []
        for i in range(expired_count):
            expired_at = timezone.now() - timedelta(minutes=10 + i)
            created_at = expired_at - timedelta(
                minutes=StockManager.RESERVATION_TTL_MINUTES
            )

            reservation = StockReservation.objects.create(
                product=product,
                quantity=expired_quantities[i],
                session_id=f"expired-{i}",
                expires_at=expired_at,
                consumed=False,
                created_at=created_at,
                updated_at=created_at,
            )
            expired_reservations.append(reservation)

        # Create active reservations
        active_reservations = []
        for i in range(active_count):
            expires_at = timezone.now() + timedelta(minutes=5 + i)
            created_at = expires_at - timedelta(
                minutes=StockManager.RESERVATION_TTL_MINUTES
            )

            reservation = StockReservation.objects.create(
                product=product,
                quantity=active_quantities[i],
                session_id=f"active-{i}",
                expires_at=expires_at,
                consumed=False,
                created_at=created_at,
                updated_at=created_at,
            )
            active_reservations.append(reservation)

        # Run cleanup
        count = StockManager.cleanup_expired_reservations()

        # Verify: Cleanup released exactly the expired reservations
        assert count == expired_count, (
            f"Cleanup should have released {expired_count} reservations, "
            f"but released {count}"
        )

        # Verify: All expired reservations are now consumed
        for i, reservation in enumerate(expired_reservations):
            reservation.refresh_from_db()
            assert reservation.consumed is True, (
                f"Expired reservation {i} should be consumed"
            )

        # Verify: All active reservations are still not consumed
        for i, reservation in enumerate(active_reservations):
            reservation.refresh_from_db()
            assert reservation.consumed is False, (
                f"Active reservation {i} should remain not consumed"
            )

        # Verify: Correct number of RELEASE log entries
        release_logs = StockLog.objects.filter(
            product=product, operation_type="RELEASE"
        )
        assert release_logs.count() == expired_count, (
            f"Should have {expired_count} RELEASE log entries"
        )

    def test_cleanup_ignores_already_consumed_reservations(self):
        """
        Test that cleanup ignores reservations that are already consumed.

        This test verifies that even if a reservation is expired, if it's
        already marked as consumed, cleanup doesn't process it again.
        """
        product = ProductFactory(stock=100)

        # Create an expired reservation that's already consumed
        expired_at = timezone.now() - timedelta(minutes=20)
        created_at = expired_at - timedelta(
            minutes=StockManager.RESERVATION_TTL_MINUTES
        )

        consumed_reservation = StockReservation.objects.create(
            product=product,
            quantity=50,
            session_id="already-consumed",
            expires_at=expired_at,
            consumed=True,  # Already consumed
            created_at=created_at,
            updated_at=created_at,
        )

        # Run cleanup
        count = StockManager.cleanup_expired_reservations()

        # Verify: Cleanup did not process the already-consumed reservation
        assert count == 0, (
            "Cleanup should not process already-consumed reservations"
        )

        # Verify: Reservation remains consumed
        consumed_reservation.refresh_from_db()
        assert consumed_reservation.consumed is True

        # Verify: No new RELEASE log entries
        release_logs = StockLog.objects.filter(
            product=product, operation_type="RELEASE"
        )
        assert release_logs.count() == 0, (
            "Should have no new RELEASE log entries for already-consumed reservation"
        )

    @pytest.mark.parametrize(
        "num_products,reservations_per_product",
        [
            (2, 1),  # 2 products, 1 expired reservation each
            (3, 2),  # 3 products, 2 expired reservations each
            (5, 3),  # 5 products, 3 expired reservations each
            (10, 1),  # 10 products, 1 expired reservation each
            (2, 5),  # 2 products, 5 expired reservations each
        ],
    )
    def test_cleanup_handles_multiple_products(
        self, num_products, reservations_per_product
    ):
        """
        Test that cleanup correctly handles expired reservations across multiple products.

        This test verifies that cleanup processes expired reservations for all
        products, not just a single product.

        Args:
            num_products: Number of products to create
            reservations_per_product: Number of expired reservations per product
        """
        # Setup: Create multiple products
        products = [ProductFactory(stock=200) for _ in range(num_products)]

        # Create expired reservations for each product
        total_expired = 0
        for product_idx, product in enumerate(products):
            for res_idx in range(reservations_per_product):
                expired_at = timezone.now() - timedelta(minutes=10 + res_idx)
                created_at = expired_at - timedelta(
                    minutes=StockManager.RESERVATION_TTL_MINUTES
                )

                StockReservation.objects.create(
                    product=product,
                    quantity=10,
                    session_id=f"product-{product_idx}-res-{res_idx}",
                    expires_at=expired_at,
                    consumed=False,
                    created_at=created_at,
                    updated_at=created_at,
                )
                total_expired += 1

        # Run cleanup
        count = StockManager.cleanup_expired_reservations()

        # Verify: Cleanup released all expired reservations across all products
        assert count == total_expired, (
            f"Cleanup should have released {total_expired} reservations "
            f"across {num_products} products, but released {count}"
        )

        # Verify: All reservations are now consumed
        for product in products:
            product_reservations = StockReservation.objects.filter(
                product=product
            )
            for reservation in product_reservations:
                assert reservation.consumed is True, (
                    f"All reservations for product {product.id} should be consumed"
                )

        # Verify: RELEASE logs created for each product
        for product in products:
            release_logs = StockLog.objects.filter(
                product=product, operation_type="RELEASE"
            )
            assert release_logs.count() == reservations_per_product, (
                f"Product {product.id} should have {reservations_per_product} "
                f"RELEASE log entries"
            )

    def test_cleanup_restores_available_stock(self):
        """
        Test that cleanup makes reserved stock available again.

        This test verifies that after cleanup releases expired reservations,
        the stock becomes available for new reservations. Note that
        get_available_stock already excludes expired reservations from its
        calculation, so expired reservations don't block new reservations
        even before cleanup. However, cleanup is still important to:
        1. Mark reservations as consumed for audit purposes
        2. Create StockLog entries for tracking
        3. Clean up the database
        """
        product = ProductFactory(stock=100)

        # Create an active reservation first
        StockManager.reserve_stock(
            product_id=product.id,
            quantity=30,
            session_id="active-reservation",
            user_id=None,
        )

        # Create an expired reservation
        expired_at = timezone.now() - timedelta(minutes=20)
        created_at = expired_at - timedelta(
            minutes=StockManager.RESERVATION_TTL_MINUTES
        )

        expired_reservation = StockReservation.objects.create(
            product=product,
            quantity=50,
            session_id="expired-reservation",
            expires_at=expired_at,
            consumed=False,
            created_at=created_at,
            updated_at=created_at,
        )

        # Check available stock before cleanup
        # get_available_stock already excludes expired reservations
        # So available = 100 - 30 (active) = 70
        # The expired reservation (50) is NOT counted
        available_before = StockManager.get_available_stock(product.id)
        assert available_before == 70, (
            f"Available stock should be 70 (100 total - 30 active reservation), "
            f"got {available_before}. Expired reservations are already excluded."
        )

        # Verify the expired reservation exists and is not consumed
        expired_reservation.refresh_from_db()
        assert expired_reservation.consumed is False, (
            "Expired reservation should not be consumed before cleanup"
        )

        # Run cleanup
        count = StockManager.cleanup_expired_reservations()
        assert count == 1, (
            f"Should clean up 1 expired reservation, cleaned {count}"
        )

        # Check available stock after cleanup
        # Should still be 70 (100 - 30 active)
        available_after = StockManager.get_available_stock(product.id)
        assert available_after == 70, (
            f"Available stock should still be 70 after cleanup, got {available_after}"
        )

        # Verify the expired reservation is now marked as consumed
        expired_reservation.refresh_from_db()
        assert expired_reservation.consumed is True, (
            "Expired reservation should be marked as consumed after cleanup"
        )

        # Verify StockLog entry was created
        release_logs = StockLog.objects.filter(
            product=product, operation_type="RELEASE"
        )
        assert release_logs.count() == 1, (
            "Should have 1 RELEASE log entry after cleanup"
        )

        # Verify we can reserve the available stock (70 units)
        new_reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=70,
            session_id="new-reservation",
            user_id=None,
        )
        assert new_reservation.quantity == 70, (
            "Should be able to reserve 70 units (the available stock)"
        )

    def test_cleanup_with_no_reservations(self):
        """
        Test that cleanup handles the case when there are no reservations.
        """
        # Run cleanup with no reservations in database
        count = StockManager.cleanup_expired_reservations()

        # Verify: Cleanup returns 0
        assert count == 0, (
            "Cleanup should return 0 when there are no reservations"
        )

    def test_cleanup_with_only_active_reservations(self):
        """
        Test that cleanup returns 0 when all reservations are active.
        """
        product = ProductFactory(stock=100)

        # Create only active reservations
        for i in range(5):
            expires_at = timezone.now() + timedelta(minutes=10 + i)
            created_at = expires_at - timedelta(
                minutes=StockManager.RESERVATION_TTL_MINUTES
            )

            StockReservation.objects.create(
                product=product,
                quantity=10,
                session_id=f"active-{i}",
                expires_at=expires_at,
                consumed=False,
                created_at=created_at,
                updated_at=created_at,
            )

        # Run cleanup
        count = StockManager.cleanup_expired_reservations()

        # Verify: Cleanup returns 0
        assert count == 0, (
            "Cleanup should return 0 when all reservations are active"
        )

        # Verify: All reservations remain not consumed
        all_reservations = StockReservation.objects.filter(product=product)
        for reservation in all_reservations:
            assert reservation.consumed is False, (
                "All active reservations should remain not consumed"
            )
