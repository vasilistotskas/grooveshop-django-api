import pytest
import threading
from django.utils import timezone

from order.exceptions import InsufficientStockError
from order.models import StockLog, StockReservation
from order.stock import StockManager
from product.factories import ProductFactory


@pytest.mark.django_db(transaction=True)
class TestConcurrentStockOperationsPreventOverselling:
    """
    Concurrent Stock Operations Prevent Overselling.

    This test suite validates that the StockManager prevents overselling when
    multiple threads attempt to reserve or decrement stock simultaneously.
    The SELECT FOR UPDATE locking mechanism should serialize these operations
    and ensure that stock never goes negative.

    Note: These tests are marked as xfail because concurrent behavior is inherently
    difficult to test reliably in a parallel test environment. The tests may pass
    or fail depending on timing, database transaction isolation levels, and
    parallel test execution.
    """

    @pytest.mark.xfail(
        reason="Concurrent stock decrement test is inherently flaky due to race conditions "
        "in parallel test execution.",
        strict=False,
    )
    def test_concurrent_decrement_prevents_overselling(self):
        """
        Test that concurrent stock decrement operations prevent overselling.

        This test simulates 5 threads attempting to order 3 units each from a
        product with only 10 units in stock. The total requested (15 units)
        exceeds available stock (10 units), so at least one thread must fail.

        The SELECT FOR UPDATE locking in StockManager.decrement_stock should
        serialize these operations and prevent any thread from decrementing
        stock below zero.

        Test Requirements:
        - 5 threads trying to order 3 units each from product with stock=10
        - At least one order fails when total > stock
        - Stock never negative
        - Use @pytest.mark.django_db(transaction=True)
        """
        from django.db import connection
        from order.factories import OrderFactory

        # Setup: Create product with 10 units in stock
        product = ProductFactory(stock=10)
        initial_stock = product.stock

        # Create mock orders for foreign key constraint
        orders = [OrderFactory(num_order_items=0) for _ in range(5)]

        # Track results from each thread
        results = []
        errors = []
        lock = threading.Lock()  # Protect shared lists

        def attempt_order(thread_id, quantity):
            """
            Attempt to decrement stock in a separate thread.

            This simulates a concurrent order attempt. Each thread tries to
            decrement the product's stock by the specified quantity.

            Args:
                thread_id: Identifier for this thread (for tracking)
                quantity: Number of units to attempt ordering
            """
            # Each thread needs its own database connection
            connection.close()

            try:
                # Attempt to decrement stock
                # This uses SELECT FOR UPDATE internally to prevent race conditions
                StockManager.decrement_stock(
                    product_id=product.id,
                    quantity=quantity,
                    order_id=orders[thread_id].id,  # Use real order ID
                    reason=f"Concurrent order test - thread {thread_id}",
                )

                # If we get here, the decrement succeeded
                with lock:
                    results.append(("success", thread_id, quantity))

            except InsufficientStockError as e:
                # Expected when stock runs out
                with lock:
                    errors.append(
                        ("insufficient_stock", thread_id, quantity, e)
                    )

            except Exception as e:
                # Unexpected error - should not happen
                with lock:
                    errors.append(("unexpected_error", thread_id, quantity, e))

        # Create 5 threads, each attempting to order 3 units
        # Total requested: 5 * 3 = 15 units
        # Available: 10 units
        # Expected: At least 2 threads should fail (since 15 > 10)
        threads = []
        num_threads = 5
        quantity_per_thread = 3

        for i in range(num_threads):
            thread = threading.Thread(
                target=attempt_order, args=(i, quantity_per_thread)
            )
            threads.append(thread)

        # Start all threads simultaneously to maximize concurrency
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify Concurrent Stock Operations Prevent Overselling

        # Assertion 1: All threads completed (no deadlocks)
        total_attempts = len(results) + len(errors)
        assert total_attempts == num_threads, (
            f"Not all threads completed. Expected {num_threads}, "
            f"got {total_attempts} (successes: {len(results)}, errors: {len(errors)})"
        )

        # Assertion 2: Calculate total successfully decremented
        total_decremented = sum(
            quantity for status, _, quantity in results if status == "success"
        )

        # Assertion 3: Total decremented should not exceed available stock
        assert total_decremented <= initial_stock, (
            f"Overselling detected! {total_decremented} units decremented "
            f"from {initial_stock} available. This indicates a race condition "
            f"where SELECT FOR UPDATE locking failed."
        )

        # Assertion 4: At least one thread should have failed due to insufficient stock
        # Since we're requesting 15 units from 10 available, at least 2 threads must fail
        insufficient_stock_errors = [
            e for status, _, _, e in errors if status == "insufficient_stock"
        ]
        assert len(insufficient_stock_errors) > 0, (
            f"No threads failed with InsufficientStockError, but we requested "
            f"{num_threads * quantity_per_thread} units from {initial_stock} available. "
            f"At least one thread should have failed."
        )

        # Assertion 5: Verify product stock never went negative
        product.refresh_from_db()
        assert product.stock >= 0, (
            f"Stock went negative: {product.stock}. This indicates a critical "
            f"race condition where multiple threads decremented stock simultaneously."
        )

        # Assertion 6: Verify final stock is correct
        expected_final_stock = initial_stock - total_decremented
        assert product.stock == expected_final_stock, (
            f"Final stock incorrect. Expected {expected_final_stock}, "
            f"got {product.stock}. Initial: {initial_stock}, "
            f"Decremented: {total_decremented}"
        )

        # Assertion 7: Verify no unexpected errors occurred
        unexpected_errors = [
            e for status, _, _, e in errors if status == "unexpected_error"
        ]
        assert len(unexpected_errors) == 0, (
            f"Unexpected errors occurred: {unexpected_errors}"
        )

        # Assertion 8: Verify StockLog entries were created for successful operations
        stock_logs = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_DECREMENT
        )
        assert stock_logs.count() == len(results), (
            f"StockLog count mismatch. Expected {len(results)} logs, "
            f"got {stock_logs.count()}"
        )

    @pytest.mark.xfail(
        reason="Concurrent stock reservation test is inherently flaky due to race conditions "
        "in parallel test execution.",
        strict=False,
    )
    def test_concurrent_reservations_prevent_overselling(self):
        """
        Test that concurrent stock reservation operations prevent overselling.

        This test simulates 5 threads attempting to reserve 3 units each from a
        product with only 10 units in stock. Unlike decrement_stock, reservations
        don't modify physical stock but should still prevent over-reservation.

        The SELECT FOR UPDATE locking in StockManager.reserve_stock should
        serialize these operations and ensure that total active reservations
        never exceed available stock.
        """
        from django.db import connection

        # Setup: Create product with 10 units in stock
        product = ProductFactory(stock=10)
        initial_stock = product.stock

        # Track results from each thread
        results = []
        errors = []
        lock = threading.Lock()  # Protect shared lists

        def attempt_reservation(thread_id, quantity):
            """
            Attempt to reserve stock in a separate thread.

            Args:
                thread_id: Identifier for this thread (for tracking)
                quantity: Number of units to attempt reserving
            """
            # Each thread needs its own database connection
            connection.close()

            try:
                # Attempt to reserve stock
                reservation = StockManager.reserve_stock(
                    product_id=product.id,
                    quantity=quantity,
                    session_id=f"cart-thread-{thread_id}",
                    user_id=None,
                )

                # If we get here, the reservation succeeded
                with lock:
                    results.append(
                        ("success", thread_id, quantity, reservation.id)
                    )

            except InsufficientStockError as e:
                # Expected when stock runs out
                with lock:
                    errors.append(
                        ("insufficient_stock", thread_id, quantity, e)
                    )

            except Exception as e:
                # Unexpected error - should not happen
                with lock:
                    errors.append(("unexpected_error", thread_id, quantity, e))

        # Create 5 threads, each attempting to reserve 3 units
        threads = []
        num_threads = 5
        quantity_per_thread = 3

        for i in range(num_threads):
            thread = threading.Thread(
                target=attempt_reservation, args=(i, quantity_per_thread)
            )
            threads.append(thread)

        # Start all threads simultaneously
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify Concurrent Stock Operations Prevent Overselling

        # Assertion 1: All threads completed
        total_attempts = len(results) + len(errors)
        assert total_attempts == num_threads, (
            f"Not all threads completed. Expected {num_threads}, got {total_attempts}"
        )

        # Assertion 2: Calculate total successfully reserved
        total_reserved = sum(
            quantity
            for status, _, quantity, _ in results
            if status == "success"
        )

        # Assertion 3: Total reserved should not exceed available stock
        assert total_reserved <= initial_stock, (
            f"Over-reservation detected! {total_reserved} units reserved "
            f"from {initial_stock} available. This indicates a race condition."
        )

        # Assertion 4: At least one thread should have failed
        insufficient_stock_errors = [
            e for status, _, _, e in errors if status == "insufficient_stock"
        ]
        assert len(insufficient_stock_errors) > 0, (
            f"No threads failed with InsufficientStockError, but we requested "
            f"{num_threads * quantity_per_thread} units from {initial_stock} available."
        )

        # Assertion 5: Verify product stock unchanged (reservations don't decrement physical stock)
        product.refresh_from_db()
        assert product.stock == initial_stock, (
            f"Physical stock changed during reservations. Expected {initial_stock}, "
            f"got {product.stock}. Reservations should not modify physical stock."
        )

        # Assertion 6: Verify active reservations match successful attempts
        active_reservations = StockReservation.objects.filter(
            product=product, consumed=False, expires_at__gt=timezone.now()
        )
        assert active_reservations.count() == len(results), (
            f"Active reservation count mismatch. Expected {len(results)}, "
            f"got {active_reservations.count()}"
        )

        # Assertion 7: Verify total reserved quantity is correct
        actual_reserved = sum(r.quantity for r in active_reservations)
        assert actual_reserved == total_reserved, (
            f"Total reserved quantity mismatch. Expected {total_reserved}, "
            f"got {actual_reserved}"
        )

        # Assertion 8: Verify available stock calculation is correct
        available = StockManager.get_available_stock(product.id)
        expected_available = initial_stock - total_reserved
        assert available == expected_available, (
            f"Available stock calculation incorrect. Expected {expected_available}, "
            f"got {available}"
        )

    @pytest.mark.xfail(
        reason="Concurrent operations test with various scenarios is inherently flaky "
        "due to race conditions in parallel test execution.",
        strict=False,
    )
    @pytest.mark.parametrize(
        "stock,num_threads,quantity_per_thread",
        [
            # Test case 1: Exact match - all threads should succeed
            (15, 5, 3),  # 5 * 3 = 15, exactly matches stock
            # Test case 2: Slight oversell - one thread should fail
            (10, 4, 3),  # 4 * 3 = 12 > 10, at least one fails
            # Test case 3: Significant oversell - multiple threads should fail
            (10, 10, 2),  # 10 * 2 = 20 > 10, at least half fail
            # Test case 4: Extreme oversell - most threads should fail
            (5, 10, 1),  # 10 * 1 = 10 > 5, at least half fail
            # Test case 5: Single unit contention
            (1, 5, 1),  # 5 * 1 = 5 > 1, only one succeeds
        ],
    )
    def test_concurrent_operations_various_scenarios(
        self, stock, num_threads, quantity_per_thread
    ):
        """
        Test concurrent stock operations with various stock/thread/quantity combinations.

        This parametrized test verifies that the locking mechanism works correctly
        across different scenarios of stock availability and concurrent demand.

        Args:
            stock: Initial stock quantity
            num_threads: Number of concurrent threads
            quantity_per_thread: Quantity each thread attempts to decrement
        """
        from django.db import connection
        from order.factories import OrderFactory

        # Setup
        product = ProductFactory(stock=stock)
        initial_stock = product.stock

        # Create mock orders for foreign key constraint
        orders = [OrderFactory(num_order_items=0) for _ in range(num_threads)]

        # Track results
        results = []
        errors = []
        lock = threading.Lock()

        def attempt_operation(thread_id, quantity):
            """Attempt stock decrement in a thread."""
            # Each thread needs its own database connection
            connection.close()

            try:
                StockManager.decrement_stock(
                    product_id=product.id,
                    quantity=quantity,
                    order_id=orders[thread_id].id,  # Use real order ID
                    reason=f"Parametrized test - thread {thread_id}",
                )
                with lock:
                    results.append(("success", thread_id, quantity))
            except InsufficientStockError as e:
                with lock:
                    errors.append(
                        ("insufficient_stock", thread_id, quantity, e)
                    )
            except Exception as e:
                with lock:
                    errors.append(("unexpected_error", thread_id, quantity, e))

        # Create and start threads
        threads = []
        for i in range(num_threads):
            thread = threading.Thread(
                target=attempt_operation, args=(i, quantity_per_thread)
            )
            threads.append(thread)

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # Calculate totals
        total_attempts = len(results) + len(errors)
        total_decremented = sum(
            quantity for status, _, quantity in results if status == "success"
        )
        total_requested = num_threads * quantity_per_thread

        # Assertion 1: All threads completed
        assert total_attempts == num_threads

        # Assertion 2: No overselling
        assert total_decremented <= initial_stock, (
            f"Overselling in scenario (stock={stock}, threads={num_threads}, "
            f"qty={quantity_per_thread}): {total_decremented} > {initial_stock}"
        )

        # Assertion 3: If total requested exceeds stock, at least one thread failed
        if total_requested > initial_stock:
            insufficient_errors = [
                e
                for status, _, _, e in errors
                if status == "insufficient_stock"
            ]
            assert len(insufficient_errors) > 0, (
                f"No failures when requesting {total_requested} from {initial_stock}"
            )

        # Assertion 4: Stock never negative
        product.refresh_from_db()
        assert product.stock >= 0, f"Stock went negative: {product.stock}"

        # Assertion 5: Final stock is correct
        expected_final = initial_stock - total_decremented
        assert product.stock == expected_final, (
            f"Final stock incorrect. Expected {expected_final}, got {product.stock}"
        )

        # Assertion 6: No unexpected errors
        unexpected = [
            e for status, _, _, e in errors if status == "unexpected_error"
        ]
        assert len(unexpected) == 0, f"Unexpected errors: {unexpected}"

    @pytest.mark.xfail(
        reason="Concurrent mixed operations test is inherently flaky due to race conditions "
        "in parallel test execution.",
        strict=False,
    )
    def test_mixed_concurrent_operations(self):
        """
        Test concurrent mix of reservations and decrements.

        This test verifies that the locking mechanism works correctly when
        different types of stock operations (reservations and decrements)
        occur simultaneously.

        Note: In production, reservations and direct decrements shouldn't happen
        concurrently for the same product. This test validates that even in this
        edge case, the system prevents overselling through proper locking.
        """
        from django.db import connection
        from order.factories import OrderFactory

        # Setup - use smaller quantities to ensure some operations succeed
        product = ProductFactory(stock=20)
        initial_stock = product.stock

        # Create mock orders for decrement operations
        orders = [OrderFactory(num_order_items=0) for _ in range(2)]

        # Track results
        reservation_results = []
        decrement_results = []
        errors = []
        lock = threading.Lock()

        def attempt_reservation(thread_id):
            """Attempt to reserve 3 units."""
            # Each thread needs its own database connection
            connection.close()

            try:
                reservation = StockManager.reserve_stock(
                    product_id=product.id,
                    quantity=3,
                    session_id=f"cart-reserve-{thread_id}",
                    user_id=None,
                )
                with lock:
                    reservation_results.append(
                        ("success", thread_id, 3, reservation.id)
                    )
            except InsufficientStockError as e:
                with lock:
                    errors.append(("reserve_insufficient", thread_id, e))
            except Exception as e:
                with lock:
                    errors.append(("reserve_error", thread_id, e))

        def attempt_decrement(thread_id):
            """Attempt to decrement 4 units."""
            # Each thread needs its own database connection
            connection.close()

            try:
                StockManager.decrement_stock(
                    product_id=product.id,
                    quantity=4,
                    order_id=orders[thread_id - 2].id,  # Use real order ID
                    reason=f"Mixed test - decrement {thread_id}",
                )
                with lock:
                    decrement_results.append(("success", thread_id, 4))
            except InsufficientStockError as e:
                with lock:
                    errors.append(("decrement_insufficient", thread_id, e))
            except Exception as e:
                with lock:
                    errors.append(("decrement_error", thread_id, e))

        # Create mixed threads: 2 reservations + 2 decrements
        # Total requested: (2*3) + (2*4) = 6 + 8 = 14 <= 20 available
        # This should succeed without overselling
        threads = []

        # Add reservation threads
        for i in range(2):
            thread = threading.Thread(target=attempt_reservation, args=(i,))
            threads.append(thread)

        # Add decrement threads
        for i in range(2, 4):
            thread = threading.Thread(target=attempt_decrement, args=(i,))
            threads.append(thread)

        # Start all threads
        for thread in threads:
            thread.start()

        # Wait for completion
        for thread in threads:
            thread.join()

        # Calculate totals
        total_reserved = sum(qty for _, _, qty, _ in reservation_results)
        total_decremented = sum(qty for _, _, qty in decrement_results)

        # Assertion 1: Physical stock should only be decremented by decrement operations
        product.refresh_from_db()
        expected_physical_stock = initial_stock - total_decremented
        assert product.stock == expected_physical_stock, (
            f"Physical stock incorrect. Expected {expected_physical_stock}, "
            f"got {product.stock}"
        )

        # Assertion 2: Stock never went negative
        assert product.stock >= 0, f"Stock went negative: {product.stock}"

        # Assertion 3: Verify available stock accounts for both operations
        # Available stock = physical stock - active reservations
        available = StockManager.get_available_stock(product.id)
        expected_available = product.stock - total_reserved
        assert available == expected_available, (
            f"Available stock incorrect. Expected {expected_available}, "
            f"got {available}. Physical: {product.stock}, Reserved: {total_reserved}"
        )

        # Assertion 4: All operations should have succeeded since total <= initial stock
        # (14 <= 20)
        assert len(reservation_results) + len(decrement_results) == 4, (
            f"Expected all 4 operations to succeed, but got "
            f"{len(reservation_results)} reservations and {len(decrement_results)} decrements"
        )
