from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone
from djmoney.money import Money

from order.models.stock_reservation import StockReservation
from order.stock import StockManager
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory


@pytest.mark.django_db(transaction=True)
class TestAvailableStockExcludesReservations:
    """
    Test that get_available_stock correctly calculates available stock by
    excluding only active reservations (not consumed and not expired).
    """

    def setup_method(self):
        """Set up test data for each test method."""
        self.user = UserAccountFactory.create()

    @pytest.mark.parametrize(
        "total_stock,active_reservations,expired_reservations,consumed_reservations,expected_available",
        [
            # No reservations - available equals total
            (100, [], [], [], 100),
            (50, [], [], [], 50),
            (0, [], [], [], 0),
            # Only active reservations - subtract from total
            (100, [10], [], [], 90),
            (100, [10, 20], [], [], 70),
            (100, [10, 20, 30], [], [], 40),
            (50, [25, 25], [], [], 0),
            (50, [10, 10, 10, 10, 10], [], [], 0),
            # Only expired reservations - don't subtract (available equals total)
            (100, [], [10], [], 100),
            (100, [], [10, 20], [], 100),
            (100, [], [10, 20, 30], [], 100),
            (50, [], [50], [], 50),
            # Only consumed reservations - don't subtract (available equals total)
            (100, [], [], [10], 100),
            (100, [], [], [10, 20], 100),
            (100, [], [], [10, 20, 30], 100),
            (50, [], [], [50], 50),
            # Mixed: active and expired - only subtract active
            (100, [10], [20], [], 90),
            (100, [10, 20], [30], [], 70),
            (100, [25], [25, 25], [], 75),
            (50, [10, 10], [10, 10], [], 30),
            # Mixed: active and consumed - only subtract active
            (100, [10], [], [20], 90),
            (100, [10, 20], [], [30], 70),
            (100, [25], [], [25, 25], 75),
            (50, [10, 10], [], [10, 10], 30),
            # Mixed: expired and consumed - don't subtract any
            (100, [], [10], [20], 100),
            (100, [], [10, 20], [30], 100),
            (50, [], [25], [25], 50),
            # Mixed: all three types - only subtract active
            (100, [10], [20], [30], 90),
            (100, [10, 20], [15], [25], 70),
            (100, [5, 10, 15], [20], [30], 70),
            (200, [50, 50], [30, 30], [20, 20], 100),
            # Edge case: all reservations active
            (100, [100], [], [], 0),
            (100, [50, 50], [], [], 0),
            # Edge case: all reservations expired
            (100, [], [100], [], 100),
            (100, [], [50, 50], [], 100),
            # Edge case: all reservations consumed
            (100, [], [], [100], 100),
            (100, [], [], [50, 50], 100),
            # Edge case: complex mixed scenario
            (500, [50, 75, 25], [100, 50], [80, 60, 40], 350),
            (1000, [100, 200, 150], [200, 100], [150, 100], 550),
        ],
        ids=[
            # No reservations
            "no_reservations_100",
            "no_reservations_50",
            "no_reservations_0",
            # Only active
            "active_10_from_100",
            "active_10_20_from_100",
            "active_10_20_30_from_100",
            "active_25_25_from_50_zero_available",
            "active_five_10s_from_50_zero_available",
            # Only expired
            "expired_10_from_100",
            "expired_10_20_from_100",
            "expired_10_20_30_from_100",
            "expired_50_from_50",
            # Only consumed
            "consumed_10_from_100",
            "consumed_10_20_from_100",
            "consumed_10_20_30_from_100",
            "consumed_50_from_50",
            # Active and expired
            "active_10_expired_20_from_100",
            "active_10_20_expired_30_from_100",
            "active_25_expired_25_25_from_100",
            "active_10_10_expired_10_10_from_50",
            # Active and consumed
            "active_10_consumed_20_from_100",
            "active_10_20_consumed_30_from_100",
            "active_25_consumed_25_25_from_100",
            "active_10_10_consumed_10_10_from_50",
            # Expired and consumed
            "expired_10_consumed_20_from_100",
            "expired_10_20_consumed_30_from_100",
            "expired_25_consumed_25_from_50",
            # All three types
            "active_10_expired_20_consumed_30_from_100",
            "active_10_20_expired_15_consumed_25_from_100",
            "active_5_10_15_expired_20_consumed_30_from_100",
            "active_50_50_expired_30_30_consumed_20_20_from_200",
            # Edge cases
            "all_active_100_from_100",
            "all_active_50_50_from_100",
            "all_expired_100_from_100",
            "all_expired_50_50_from_100",
            "all_consumed_100_from_100",
            "all_consumed_50_50_from_100",
            # Complex scenarios
            "complex_500_stock",
            "complex_1000_stock",
        ],
    )
    def test_get_available_stock_calculation(
        self,
        total_stock,
        active_reservations,
        expired_reservations,
        consumed_reservations,
        expected_available,
    ):
        """
        Test that get_available_stock correctly calculates available stock.

        The calculation should be:
        available_stock = total_stock - sum(active_reservation_quantities)

        Where active reservations are:
        - Not consumed (consumed=False)
        - Not expired (expires_at > now)
        """
        # Create product with specified total stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=total_stock
        )
        product.set_current_language("en")
        product.name = f"Test Product (Stock: {total_stock})"
        product.save()

        now = timezone.now()

        # Create active reservations (not consumed, not expired)
        for i, quantity in enumerate(active_reservations):
            StockReservation.objects.create(
                product=product,
                quantity=quantity,
                reserved_by=self.user,
                session_id=f"active-session-{i}",
                expires_at=now + timedelta(minutes=10),  # Expires in future
                consumed=False,
            )

        # Create expired reservations (not consumed, expired)
        for i, quantity in enumerate(expired_reservations):
            StockReservation.objects.create(
                product=product,
                quantity=quantity,
                reserved_by=self.user,
                session_id=f"expired-session-{i}",
                expires_at=now - timedelta(minutes=10),  # Expired in past
                consumed=False,
            )

        # Create consumed reservations (consumed, any expiration)
        for i, quantity in enumerate(consumed_reservations):
            StockReservation.objects.create(
                product=product,
                quantity=quantity,
                reserved_by=self.user,
                session_id=f"consumed-session-{i}",
                expires_at=now
                + timedelta(minutes=10),  # Expiration doesn't matter
                consumed=True,
            )

        # Calculate available stock
        available = StockManager.get_available_stock(product.id)

        # Verify calculation is correct
        assert available == expected_available, (
            f"Available stock calculation incorrect. "
            f"Expected {expected_available}, got {available}. "
            f"Total stock: {total_stock}, "
            f"Active reservations: {active_reservations} (sum={sum(active_reservations)}), "
            f"Expired reservations: {expired_reservations} (sum={sum(expired_reservations)}), "
            f"Consumed reservations: {consumed_reservations} (sum={sum(consumed_reservations)})"
        )

        # Verify the calculation formula explicitly
        expected_calculation = total_stock - sum(active_reservations)
        assert available == expected_calculation, (
            f"Available stock doesn't match formula: "
            f"total_stock ({total_stock}) - sum(active_reservations) ({sum(active_reservations)}) "
            f"= {expected_calculation}, but got {available}"
        )

    def test_available_stock_excludes_only_active_reservations(self):
        """
        Test that available stock calculation excludes only active reservations.

        This test explicitly verifies that:
        1. Active reservations (not consumed, not expired) ARE excluded
        2. Expired reservations (not consumed, expired) are NOT excluded
        3. Consumed reservations (consumed, any expiration) are NOT excluded
        """
        # Create product with 100 units
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=100
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        now = timezone.now()

        # Create 1 active reservation (10 units)
        StockReservation.objects.create(
            product=product,
            quantity=10,
            reserved_by=self.user,
            session_id="active-session",
            expires_at=now + timedelta(minutes=10),
            consumed=False,
        )

        # Create 1 expired reservation (20 units)
        StockReservation.objects.create(
            product=product,
            quantity=20,
            reserved_by=self.user,
            session_id="expired-session",
            expires_at=now - timedelta(minutes=10),
            consumed=False,
        )

        # Create 1 consumed reservation (30 units)
        StockReservation.objects.create(
            product=product,
            quantity=30,
            reserved_by=self.user,
            session_id="consumed-session",
            expires_at=now + timedelta(minutes=10),
            consumed=True,
        )

        # Calculate available stock
        available = StockManager.get_available_stock(product.id)

        # Should be: 100 - 10 (active only) = 90
        assert available == 90, (
            f"Available stock should exclude only active reservations. "
            f"Expected 90 (100 - 10 active), got {available}"
        )

        # Verify by checking each reservation type
        active_count = StockReservation.objects.filter(
            product=product, consumed=False, expires_at__gt=now
        ).count()
        assert active_count == 1, (
            f"Should have 1 active reservation, got {active_count}"
        )

        expired_count = StockReservation.objects.filter(
            product=product, consumed=False, expires_at__lte=now
        ).count()
        assert expired_count == 1, (
            f"Should have 1 expired reservation, got {expired_count}"
        )

        consumed_count = StockReservation.objects.filter(
            product=product, consumed=True
        ).count()
        assert consumed_count == 1, (
            f"Should have 1 consumed reservation, got {consumed_count}"
        )

    def test_available_stock_updates_when_reservation_expires(self):
        """
        Test that available stock increases when a reservation expires.

        When a reservation expires (expires_at becomes < now), it should no longer
        be counted as active, and available stock should increase accordingly.
        """
        # Create product with 100 units
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=100
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        now = timezone.now()

        # Create reservation that will expire in 1 second
        reservation = StockReservation.objects.create(
            product=product,
            quantity=25,
            reserved_by=self.user,
            session_id="expiring-session",
            expires_at=now + timedelta(seconds=1),
            consumed=False,
        )

        # Check available stock before expiration
        available_before = StockManager.get_available_stock(product.id)
        assert available_before == 75, (
            f"Before expiration, available should be 75 (100 - 25), got {available_before}"
        )

        # Manually update reservation to be expired
        # (In production, this would happen naturally as time passes)
        reservation.expires_at = now - timedelta(seconds=1)
        reservation.save()

        # Check available stock after expiration
        available_after = StockManager.get_available_stock(product.id)
        assert available_after == 100, (
            f"After expiration, available should be 100 (no active reservations), got {available_after}"
        )

    def test_available_stock_updates_when_reservation_consumed(self):
        """
        Test that available stock increases when a reservation is consumed.

        When a reservation is consumed (converted to sale), it should no longer
        be counted as active, and available stock should increase accordingly.
        Note: The physical stock will be decremented, but the reservation is
        no longer blocking additional reservations.
        """
        # Create product with 100 units
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=100
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        now = timezone.now()

        # Create active reservation
        reservation = StockReservation.objects.create(
            product=product,
            quantity=25,
            reserved_by=self.user,
            session_id="to-be-consumed-session",
            expires_at=now + timedelta(minutes=10),
            consumed=False,
        )

        # Check available stock before consumption
        available_before = StockManager.get_available_stock(product.id)
        assert available_before == 75, (
            f"Before consumption, available should be 75 (100 - 25), got {available_before}"
        )

        # Mark reservation as consumed (simulating payment success)
        reservation.consumed = True
        reservation.save()

        # Also decrement physical stock (as would happen in convert_reservation_to_sale)
        product.stock -= reservation.quantity
        product.save()

        # Check available stock after consumption
        # Should be: 75 (new physical stock) - 0 (no active reservations) = 75
        available_after = StockManager.get_available_stock(product.id)
        assert available_after == 75, (
            f"After consumption, available should be 75 (75 physical - 0 active), got {available_after}"
        )

    @pytest.mark.parametrize(
        "scenario_name,stock,reservations",
        [
            (
                "single_active",
                100,
                [{"quantity": 10, "consumed": False, "expired": False}],
            ),
            (
                "multiple_active",
                100,
                [
                    {"quantity": 10, "consumed": False, "expired": False},
                    {"quantity": 20, "consumed": False, "expired": False},
                    {"quantity": 15, "consumed": False, "expired": False},
                ],
            ),
            (
                "mixed_states",
                200,
                [
                    {
                        "quantity": 30,
                        "consumed": False,
                        "expired": False,
                    },  # Active
                    {
                        "quantity": 40,
                        "consumed": False,
                        "expired": True,
                    },  # Expired
                    {
                        "quantity": 50,
                        "consumed": True,
                        "expired": False,
                    },  # Consumed
                    {
                        "quantity": 25,
                        "consumed": False,
                        "expired": False,
                    },  # Active
                    {
                        "quantity": 35,
                        "consumed": True,
                        "expired": True,
                    },  # Consumed & expired
                ],
            ),
            (
                "all_expired",
                150,
                [
                    {"quantity": 50, "consumed": False, "expired": True},
                    {"quantity": 30, "consumed": False, "expired": True},
                    {"quantity": 20, "consumed": False, "expired": True},
                ],
            ),
            (
                "all_consumed",
                150,
                [
                    {"quantity": 50, "consumed": True, "expired": False},
                    {"quantity": 30, "consumed": True, "expired": False},
                    {"quantity": 20, "consumed": True, "expired": False},
                ],
            ),
        ],
        ids=[
            "single_active_reservation",
            "multiple_active_reservations",
            "mixed_reservation_states",
            "all_expired_reservations",
            "all_consumed_reservations",
        ],
    )
    def test_available_stock_with_reservation_scenarios(
        self, scenario_name, stock, reservations
    ):
        """
        Test available stock calculation with various reservation scenarios.

        This test uses a more flexible parametrization format to test
        complex scenarios with different combinations of reservation states.
        """
        # Create product
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=stock
        )
        product.set_current_language("en")
        product.name = f"Test Product - {scenario_name}"
        product.save()

        now = timezone.now()
        expected_active_sum = 0

        # Create reservations based on scenario
        for i, res_config in enumerate(reservations):
            quantity = res_config["quantity"]
            consumed = res_config["consumed"]
            expired = res_config["expired"]

            # Determine expiration time
            if expired:
                expires_at = now - timedelta(minutes=10)
            else:
                expires_at = now + timedelta(minutes=10)

            # Create reservation
            StockReservation.objects.create(
                product=product,
                quantity=quantity,
                reserved_by=self.user,
                session_id=f"{scenario_name}-session-{i}",
                expires_at=expires_at,
                consumed=consumed,
            )

            # Track expected active reservations
            if not consumed and not expired:
                expected_active_sum += quantity

        # Calculate available stock
        available = StockManager.get_available_stock(product.id)

        # Expected available = stock - sum of active reservations
        expected_available = stock - expected_active_sum

        # Verify calculation
        assert available == expected_available, (
            f"Scenario '{scenario_name}': Available stock incorrect. "
            f"Expected {expected_available} (stock {stock} - active {expected_active_sum}), "
            f"got {available}"
        )

    def test_available_stock_with_zero_stock(self):
        """
        Test available stock calculation when product has zero stock.

        Even with zero stock, the calculation should work correctly.
        Available stock should be 0 regardless of reservations.
        """
        # Create product with zero stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=0
        )
        product.set_current_language("en")
        product.name = "Out of Stock Product"
        product.save()

        # Calculate available stock (should be 0)
        available = StockManager.get_available_stock(product.id)
        assert available == 0, f"Available stock should be 0, got {available}"

        # Even with expired reservations, should still be 0
        now = timezone.now()
        StockReservation.objects.create(
            product=product,
            quantity=10,
            reserved_by=self.user,
            session_id="expired-on-zero-stock",
            expires_at=now - timedelta(minutes=10),
            consumed=False,
        )

        available_with_expired = StockManager.get_available_stock(product.id)
        assert available_with_expired == 0, (
            f"Available stock should still be 0 with expired reservations, got {available_with_expired}"
        )

    def test_available_stock_calculation_is_consistent(self):
        """
        Test that multiple calls to get_available_stock return consistent results.

        The calculation should be deterministic and return the same result
        when called multiple times without changes to reservations.
        """
        # Create product with reservations
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=100
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        now = timezone.now()

        # Create some active reservations
        for i in range(3):
            StockReservation.objects.create(
                product=product,
                quantity=10,
                reserved_by=self.user,
                session_id=f"consistency-test-{i}",
                expires_at=now + timedelta(minutes=10),
                consumed=False,
            )

        # Call get_available_stock multiple times
        results = [
            StockManager.get_available_stock(product.id) for _ in range(5)
        ]

        # All results should be identical
        assert len(set(results)) == 1, (
            f"get_available_stock returned inconsistent results: {results}"
        )

        # And should be correct (100 - 30 = 70)
        assert results[0] == 70, f"Expected 70, got {results[0]}"
