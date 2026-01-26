import pytest
from datetime import timedelta
from django.utils import timezone

from order.stock import StockManager
from order.models import StockReservation
from product.factories import ProductFactory


@pytest.mark.django_db
class TestAvailableStockExcludesReservations:
    """
    Available Stock Excludes Reservations.

    This test suite validates that the get_available_stock method correctly
    calculates available stock by excluding active reservations (not consumed
    and not expired) from the total stock.
    """

    @pytest.mark.parametrize(
        "total_stock,active_reservations,expired_reservations,consumed_reservations,description",
        [
            # Basic scenarios
            (100, [], [], [], "No reservations - all stock available"),
            (100, [25], [], [], "One active reservation"),
            (100, [25, 25], [], [], "Two active reservations"),
            (100, [10, 20, 30], [], [], "Three active reservations"),
            # With expired reservations (should not affect availability)
            (100, [25], [10], [], "Active and expired - expired ignored"),
            (100, [25], [10, 15], [], "Active and multiple expired"),
            (100, [], [50], [], "Only expired - all stock available"),
            (100, [], [25, 25, 25], [], "Multiple expired only"),
            # With consumed reservations (should not affect availability)
            (100, [25], [], [10], "Active and consumed - consumed ignored"),
            (100, [25], [], [10, 15], "Active and multiple consumed"),
            (100, [], [], [50], "Only consumed - all stock available"),
            (100, [], [], [25, 25, 25], "Multiple consumed only"),
            # Mixed scenarios
            (100, [20], [10], [15], "Active, expired, and consumed mixed"),
            (100, [10, 15], [5, 10], [20], "Multiple of each type"),
            (100, [25, 25], [25], [25], "Equal amounts of each type"),
            # Edge cases
            (100, [100], [], [], "All stock reserved"),
            (100, [50, 50], [], [], "All stock reserved by multiple"),
            (100, [1], [], [], "Minimal active reservation"),
            (
                1000,
                [250, 250, 250],
                [100],
                [100],
                "Large stock with multiple reservations",
            ),
            (10, [5], [2], [2], "Small stock"),
            (0, [], [], [], "Zero stock"),
            # Boundary cases
            (100, [99], [], [], "Almost all reserved"),
            (100, [1, 1, 1, 1, 1], [], [], "Many small reservations"),
            (100, [], [100], [], "All expired - should be available"),
            (100, [], [], [100], "All consumed - should be available"),
        ],
    )
    def test_available_stock_calculation_with_various_reservation_states(
        self,
        total_stock,
        active_reservations,
        expired_reservations,
        consumed_reservations,
        description,
    ):
        """
        Test that get_available_stock correctly excludes only active reservations.

        This test verifies that available stock is calculated as:
            available = total_stock - sum(active_reservation_quantities)

        Where active reservations are:
        - Not consumed (consumed=False)
        - Not expired (expires_at > now)

        Args:
            total_stock: Total physical stock of the product
            active_reservations: List of quantities for active reservations
            expired_reservations: List of quantities for expired reservations
            consumed_reservations: List of quantities for consumed reservations
            description: Human-readable description of the test case

        Test Requirements:
        - Create products with various reservation states (active, expired, consumed)
        - Use @pytest.mark.parametrize for different scenarios
        - Verify: Available = total - active_reservations
        """
        # Setup: Create product with specified stock
        product = ProductFactory(stock=total_stock)

        now = timezone.now()

        # Create active reservations (not consumed, not expired)
        for i, quantity in enumerate(active_reservations):
            StockReservation.objects.create(
                product=product,
                quantity=quantity,
                session_id=f"active-{i}",
                expires_at=now + timedelta(minutes=10),  # Expires in future
                consumed=False,
            )

        # Create expired reservations (not consumed, but expired)
        for i, quantity in enumerate(expired_reservations):
            StockReservation.objects.create(
                product=product,
                quantity=quantity,
                session_id=f"expired-{i}",
                expires_at=now - timedelta(minutes=10),  # Expired in past
                consumed=False,
            )

        # Create consumed reservations (consumed, regardless of expiration)
        for i, quantity in enumerate(consumed_reservations):
            StockReservation.objects.create(
                product=product,
                quantity=quantity,
                session_id=f"consumed-{i}",
                expires_at=now + timedelta(minutes=10),  # Could be any time
                consumed=True,
            )

        # Calculate expected available stock
        # Only active reservations should be subtracted
        total_active_reserved = sum(active_reservations)
        expected_available = total_stock - total_active_reserved

        # Execute: Get available stock
        available = StockManager.get_available_stock(product_id=product.id)

        # Verify: Available stock equals total minus active reservations only
        assert available == expected_available, (
            f"Available stock calculation incorrect for '{description}'. "
            f"Expected {expected_available} (total {total_stock} - active {total_active_reserved}), "
            f"but got {available}. "
            f"Active reservations: {active_reservations}, "
            f"Expired reservations: {expired_reservations}, "
            f"Consumed reservations: {consumed_reservations}"
        )

        # Verify: Physical stock unchanged (get_available_stock is read-only)
        product.refresh_from_db()
        assert product.stock == total_stock, (
            f"Physical stock should remain unchanged at {total_stock} for '{description}'"
        )

    @pytest.mark.parametrize(
        "stock,reservation_quantity,minutes_until_expiry,description",
        [
            # Just about to expire
            (100, 25, 1, "Expires in 1 minute - still active"),
            (100, 50, 0.5, "Expires in 30 seconds - still active"),
            # Just expired
            (100, 25, -0.5, "Expired 30 seconds ago - not active"),
            (100, 50, -1, "Expired 1 minute ago - not active"),
            # Well within TTL
            (100, 30, 10, "Expires in 10 minutes - active"),
            (100, 40, 14, "Expires in 14 minutes - active"),
            # Long expired
            (100, 20, -60, "Expired 1 hour ago - not active"),
            (100, 15, -1440, "Expired 1 day ago - not active"),
        ],
    )
    def test_available_stock_respects_reservation_expiration(
        self, stock, reservation_quantity, minutes_until_expiry, description
    ):
        """
        Test that get_available_stock correctly handles reservation expiration.

        This test verifies that reservations are only considered active if
        their expires_at timestamp is in the future. Expired reservations
        should not reduce available stock.

        Args:
            stock: Total physical stock
            reservation_quantity: Quantity reserved
            minutes_until_expiry: Minutes until/since expiration (negative = expired)
            description: Human-readable description
        """
        # Setup: Create product
        product = ProductFactory(stock=stock)

        # Create reservation with specific expiration time
        now = timezone.now()
        expires_at = now + timedelta(minutes=minutes_until_expiry)

        StockReservation.objects.create(
            product=product,
            quantity=reservation_quantity,
            session_id="test-expiry",
            expires_at=expires_at,
            consumed=False,
        )

        # Calculate expected available stock
        is_active = minutes_until_expiry > 0  # Active if expires in future
        expected_available = (
            stock if not is_active else stock - reservation_quantity
        )

        # Execute: Get available stock
        available = StockManager.get_available_stock(product_id=product.id)

        # Verify: Available stock correct based on expiration
        assert available == expected_available, (
            f"Available stock incorrect for '{description}'. "
            f"Expected {expected_available}, got {available}. "
            f"Reservation {'is' if is_active else 'is not'} active."
        )

    @pytest.mark.parametrize(
        "stock,reservation_quantity,is_consumed,description",
        [
            # Not consumed - should reduce availability
            (100, 25, False, "Not consumed - reduces availability"),
            (100, 50, False, "Not consumed - reduces availability"),
            (100, 100, False, "Not consumed - all reserved"),
            # Consumed - should not reduce availability
            (100, 25, True, "Consumed - does not reduce availability"),
            (100, 50, True, "Consumed - does not reduce availability"),
            (100, 100, True, "Consumed - all stock available"),
        ],
    )
    def test_available_stock_respects_consumed_flag(
        self, stock, reservation_quantity, is_consumed, description
    ):
        """
        Test that get_available_stock correctly handles consumed reservations.

        This test verifies that consumed reservations (those that have been
        converted to actual stock decrements) do not reduce available stock.

        Args:
            stock: Total physical stock
            reservation_quantity: Quantity reserved
            is_consumed: Whether the reservation is consumed
            description: Human-readable description
        """
        # Setup: Create product
        product = ProductFactory(stock=stock)

        # Create reservation with specific consumed state
        now = timezone.now()

        StockReservation.objects.create(
            product=product,
            quantity=reservation_quantity,
            session_id="test-consumed",
            expires_at=now + timedelta(minutes=10),  # Not expired
            consumed=is_consumed,
        )

        # Calculate expected available stock
        # Consumed reservations should not reduce availability
        expected_available = (
            stock if is_consumed else stock - reservation_quantity
        )

        # Execute: Get available stock
        available = StockManager.get_available_stock(product_id=product.id)

        # Verify: Available stock correct based on consumed flag
        assert available == expected_available, (
            f"Available stock incorrect for '{description}'. "
            f"Expected {expected_available}, got {available}. "
            f"Reservation {'is' if is_consumed else 'is not'} consumed."
        )

    def test_available_stock_with_multiple_products(self):
        """
        Test that get_available_stock correctly isolates reservations by product.

        This test verifies that reservations for one product do not affect
        the available stock calculation for another product.
        """
        # Setup: Create two products
        product1 = ProductFactory(stock=100)
        product2 = ProductFactory(stock=200)

        now = timezone.now()

        # Create reservations for product1
        StockReservation.objects.create(
            product=product1,
            quantity=30,
            session_id="product1-reservation",
            expires_at=now + timedelta(minutes=10),
            consumed=False,
        )

        # Create reservations for product2
        StockReservation.objects.create(
            product=product2,
            quantity=50,
            session_id="product2-reservation",
            expires_at=now + timedelta(minutes=10),
            consumed=False,
        )

        # Execute: Get available stock for both products
        available1 = StockManager.get_available_stock(product_id=product1.id)
        available2 = StockManager.get_available_stock(product_id=product2.id)

        # Verify: Each product's availability is calculated independently
        assert available1 == 70, (
            f"Product1 should have 70 available (100 - 30), got {available1}"
        )
        assert available2 == 150, (
            f"Product2 should have 150 available (200 - 50), got {available2}"
        )

    def test_available_stock_is_read_only_operation(self):
        """
        Test that get_available_stock does not modify any data.

        This test verifies that calling get_available_stock multiple times
        does not change the product stock or reservation states.
        """
        # Setup: Create product with reservation
        product = ProductFactory(stock=100)

        now = timezone.now()
        reservation = StockReservation.objects.create(
            product=product,
            quantity=30,
            session_id="read-only-test",
            expires_at=now + timedelta(minutes=10),
            consumed=False,
        )

        # Execute: Call get_available_stock multiple times
        available1 = StockManager.get_available_stock(product_id=product.id)
        available2 = StockManager.get_available_stock(product_id=product.id)
        available3 = StockManager.get_available_stock(product_id=product.id)

        # Verify: All calls return the same value
        assert available1 == available2 == available3 == 70, (
            "Multiple calls to get_available_stock should return the same value"
        )

        # Verify: Product stock unchanged
        product.refresh_from_db()
        assert product.stock == 100, "Product stock should remain unchanged"

        # Verify: Reservation unchanged
        reservation.refresh_from_db()
        assert reservation.consumed is False, (
            "Reservation consumed flag should remain unchanged"
        )
        assert reservation.quantity == 30, (
            "Reservation quantity should remain unchanged"
        )

    @pytest.mark.parametrize(
        "num_active,num_expired,num_consumed,quantities",
        [
            # Many reservations of same type
            (10, 0, 0, [5] * 10),  # 10 active reservations of 5 each
            (0, 10, 0, [5] * 10),  # 10 expired reservations
            (0, 0, 10, [5] * 10),  # 10 consumed reservations
            # Mixed quantities
            (
                5,
                5,
                5,
                [10, 20, 30, 5, 15],
            ),  # 5 of each type with varying quantities
            # Large number of reservations
            (20, 10, 10, [1] * 40),  # 40 reservations of 1 each
        ],
    )
    def test_available_stock_with_many_reservations(
        self, num_active, num_expired, num_consumed, quantities
    ):
        """
        Test that get_available_stock handles many reservations efficiently.

        This test verifies that the calculation works correctly even with
        a large number of reservations of different types.

        Args:
            num_active: Number of active reservations to create
            num_expired: Number of expired reservations to create
            num_consumed: Number of consumed reservations to create
            quantities: List of quantities to cycle through
        """
        # Setup: Create product with large stock
        total_stock = 1000
        product = ProductFactory(stock=total_stock)

        now = timezone.now()
        total_active_quantity = 0

        # Create active reservations
        for i in range(num_active):
            quantity = quantities[i % len(quantities)]
            total_active_quantity += quantity
            StockReservation.objects.create(
                product=product,
                quantity=quantity,
                session_id=f"active-{i}",
                expires_at=now + timedelta(minutes=10),
                consumed=False,
            )

        # Create expired reservations
        for i in range(num_expired):
            quantity = quantities[(num_active + i) % len(quantities)]
            StockReservation.objects.create(
                product=product,
                quantity=quantity,
                session_id=f"expired-{i}",
                expires_at=now - timedelta(minutes=10),
                consumed=False,
            )

        # Create consumed reservations
        for i in range(num_consumed):
            quantity = quantities[
                (num_active + num_expired + i) % len(quantities)
            ]
            StockReservation.objects.create(
                product=product,
                quantity=quantity,
                session_id=f"consumed-{i}",
                expires_at=now + timedelta(minutes=10),
                consumed=True,
            )

        # Calculate expected available stock
        expected_available = total_stock - total_active_quantity

        # Execute: Get available stock
        available = StockManager.get_available_stock(product_id=product.id)

        # Verify: Available stock correct
        assert available == expected_available, (
            f"Available stock incorrect with many reservations. "
            f"Expected {expected_available}, got {available}. "
            f"Active: {num_active}, Expired: {num_expired}, Consumed: {num_consumed}"
        )
