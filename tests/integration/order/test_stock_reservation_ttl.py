import pytest
from datetime import timedelta
from django.utils import timezone

from order.stock import StockManager
from product.factories import ProductFactory


@pytest.mark.django_db
class TestStockReservationsHaveCorrectTTL:
    """
    Stock Reservations Have Correct TTL.

    This test suite validates that all stock reservations are created with
    the correct expiration time (created_at + 15 minutes), regardless of
    when they are created.
    """

    @pytest.mark.parametrize(
        "quantity,description",
        [
            # Test various quantities
            (1, "Single unit reservation"),
            (10, "Ten units reservation"),
            (25, "Quarter stock reservation"),
            (50, "Half stock reservation"),
            (75, "Large order reservation"),
            (100, "Full stock reservation"),
            # Test edge cases
            (5, "Small quantity"),
            (99, "Almost full stock"),
            (33, "Odd number quantity"),
        ],
    )
    def test_reservation_ttl_is_15_minutes(self, quantity, description):
        """
        Test that reservation expires_at = created_at + 15 minutes.

        This test verifies that the reservation's expiration time is always
        exactly 15 minutes after the creation time, regardless of quantity.

        Args:
            quantity: The quantity to reserve
            description: Human-readable description of the test case

        Test Requirements:
        - Reservation expires_at = created_at + 15 minutes
        - Use @pytest.mark.parametrize with various creation times
        - Verify: expires_at timestamp correct for each case
        """
        # Setup: Create product with sufficient stock
        product = ProductFactory(stock=100)
        session_id = f"cart-{quantity}-{description}"

        # Record time before reservation
        time_before = timezone.now()

        # Create reservation
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=quantity,
            session_id=session_id,
            user_id=None,
        )

        # Record time after reservation
        time_after = timezone.now()

        # Calculate expected expiration time
        expected_expires_at = reservation.created_at + timedelta(
            minutes=StockManager.get_reservation_ttl_minutes()
        )

        # Verify: expires_at equals created_at + 15 minutes
        # Allow 1 second tolerance for database timestamp precision
        time_diff = abs(
            (reservation.expires_at - expected_expires_at).total_seconds()
        )
        assert time_diff < 1, (
            f"Reservation TTL incorrect for {description}. "
            f"Expected expires_at: {expected_expires_at}, "
            f"Actual expires_at: {reservation.expires_at}, "
            f"Difference: {time_diff} seconds"
        )

        # Also verify the TTL is approximately 15 minutes
        # Allow 2 second tolerance for database timestamp precision and test execution time
        actual_ttl_minutes = (
            reservation.expires_at - reservation.created_at
        ).total_seconds() / 60

        assert abs(actual_ttl_minutes - 15.0) < 0.05, (
            f"TTL not approximately 15 minutes for {description}. "
            f"Expected: ~15 minutes, "
            f"Actual: {actual_ttl_minutes:.4f} minutes"
        )

        # Verify reservation was created within test execution time
        assert time_before <= reservation.created_at <= time_after, (
            f"Reservation created_at outside expected range for {description}"
        )

    @pytest.mark.parametrize(
        "stock,quantity,session_suffix",
        [
            (100, 1, "single-unit"),
            (100, 10, "ten-units"),
            (100, 50, "half-stock"),
            (100, 100, "full-stock"),
            (50, 25, "quarter-stock"),
            (200, 75, "large-order"),
        ],
    )
    def test_ttl_consistent_across_quantities(
        self, stock, quantity, session_suffix
    ):
        """
        Test that TTL is 15 minutes regardless of reservation quantity.

        This test verifies that the expiration time is always 15 minutes,
        independent of the quantity being reserved or the total stock available.

        Args:
            stock: Total product stock
            quantity: Quantity to reserve
            session_suffix: Unique session identifier suffix
        """
        # Setup: Create product with specified stock
        product = ProductFactory(stock=stock)
        session_id = f"cart-quantity-{session_suffix}"

        # Record time before reservation
        time_before = timezone.now()

        # Create reservation
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=quantity,
            session_id=session_id,
            user_id=None,
        )

        # Record time after reservation
        time_after = timezone.now()

        # Calculate expected expiration range
        # (accounting for test execution time)
        expected_min = time_before + timedelta(minutes=15)
        expected_max = time_after + timedelta(minutes=15)

        # Verify: expires_at is within expected range
        assert expected_min <= reservation.expires_at <= expected_max, (
            f"Reservation TTL incorrect for quantity {quantity}. "
            f"Expected between {expected_min} and {expected_max}, "
            f"Got: {reservation.expires_at}"
        )

        # Verify: TTL is approximately 15 minutes
        actual_ttl = (
            reservation.expires_at - reservation.created_at
        ).total_seconds()
        expected_ttl = 15 * 60  # 15 minutes in seconds

        # Allow 2 seconds tolerance for test execution time
        assert abs(actual_ttl - expected_ttl) < 2, (
            f"TTL not 15 minutes for quantity {quantity}. "
            f"Expected: {expected_ttl}s, Actual: {actual_ttl}s"
        )

    def test_multiple_reservations_have_independent_ttls(self):
        """
        Test that multiple reservations created at different times have
        independent TTLs based on their own creation times.

        This ensures that each reservation's TTL is calculated from its own
        created_at timestamp, not from some global reference time.
        """
        product = ProductFactory(stock=100)

        # Create first reservation
        reservation1 = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-1",
            user_id=None,
        )
        expected_expires1 = reservation1.created_at + timedelta(minutes=15)

        # Small delay to ensure different timestamps
        import time

        time.sleep(0.1)

        # Create second reservation
        reservation2 = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-2",
            user_id=None,
        )
        expected_expires2 = reservation2.created_at + timedelta(minutes=15)

        # Small delay to ensure different timestamps
        time.sleep(0.1)

        # Create third reservation
        reservation3 = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-3",
            user_id=None,
        )
        expected_expires3 = reservation3.created_at + timedelta(minutes=15)

        # Verify each reservation has correct independent TTL
        assert (
            abs((reservation1.expires_at - expected_expires1).total_seconds())
            < 1
        ), "Reservation 1 TTL incorrect"

        assert (
            abs((reservation2.expires_at - expected_expires2).total_seconds())
            < 1
        ), "Reservation 2 TTL incorrect"

        assert (
            abs((reservation3.expires_at - expected_expires3).total_seconds())
            < 1
        ), "Reservation 3 TTL incorrect"

        # Verify the reservations were created at different times
        assert reservation2.created_at > reservation1.created_at, (
            "Reservation 2 should be created after Reservation 1"
        )

        assert reservation3.created_at > reservation2.created_at, (
            "Reservation 3 should be created after Reservation 2"
        )

        # Verify the reservations expire at different times
        assert reservation2.expires_at > reservation1.expires_at, (
            "Reservation 2 should expire after Reservation 1"
        )

        assert reservation3.expires_at > reservation2.expires_at, (
            "Reservation 3 should expire after Reservation 2"
        )

    def test_ttl_uses_configured_constant(self):
        """
        Test that TTL uses StockManager.get_reservation_ttl_minutes() constant.

        This ensures the TTL is configurable and not hardcoded in multiple places.
        """
        product = ProductFactory(stock=100)

        # Get the configured TTL
        configured_ttl_minutes = StockManager.get_reservation_ttl_minutes()

        # Create reservation
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-config-test",
            user_id=None,
        )

        # Calculate expected expiration using the constant
        expected_expires_at = reservation.created_at + timedelta(
            minutes=configured_ttl_minutes
        )

        # Verify reservation uses the configured TTL
        time_diff = abs(
            (reservation.expires_at - expected_expires_at).total_seconds()
        )
        assert time_diff < 1, (
            f"Reservation does not use configured TTL constant. "
            f"Expected TTL: {configured_ttl_minutes} minutes, "
            f"Time difference: {time_diff}s"
        )

        # Verify the constant is 15 minutes as per requirement
        assert configured_ttl_minutes == 15, (
            f"STOCK_RESERVATION_TTL_MINUTES should be 15, got {configured_ttl_minutes}"
        )
