import threading

import pytest
from django.db import connection

from cart.factories import CartFactory, CartItemFactory
from order.exceptions import InsufficientStockError
from order.services import OrderService
from order.stock import StockManager
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from user.factories import UserAccountFactory


@pytest.mark.django_db(transaction=True)
class TestProperty2ConcurrentStockOperationsPreventOverselling:
    """
    Test that concurrent stock operations prevent overselling through proper
    database-level locking (SELECT FOR UPDATE). Multiple threads attempting
    to purchase the same product should result in at least one failure when
    total requested quantity exceeds available stock.
    """

    def test_concurrent_reserve_stock_prevents_overselling(self):
        """
        Test: 5 threads trying to reserve 3 units each from product with stock=10.

        Expected behavior:
        - Total requested: 5 threads × 3 units = 15 units
        - Available stock: 10 units
        - At least 2 threads should fail (since 15 > 10)
        - Stock should never go negative
        - Successful reservations should total ≤ 10 units
        """
        # Create product with 10 units in stock
        product = ProductFactory(stock=10)

        # Track results from each thread
        results = []
        lock = threading.Lock()

        def reserve_stock_thread(thread_id: int, quantity: int):
            """
            Thread function to attempt stock reservation.

            Each thread tries to reserve stock and records success/failure.
            Uses a lock to safely append results from multiple threads.
            """
            # Each thread needs its own database connection
            # Django's connection is thread-local, so we need to close it
            # to force a new connection in each thread
            connection.close()

            try:
                # Attempt to reserve stock
                reservation = StockManager.reserve_stock(
                    product_id=product.id,
                    quantity=quantity,
                    session_id=f"thread-{thread_id}",
                    user_id=None,
                )

                # Record success
                with lock:
                    results.append(
                        {
                            "thread_id": thread_id,
                            "success": True,
                            "reservation_id": reservation.id,
                            "quantity": quantity,
                        }
                    )
            except InsufficientStockError:
                # Record failure (expected for some threads)
                with lock:
                    results.append(
                        {
                            "thread_id": thread_id,
                            "success": False,
                            "quantity": quantity,
                        }
                    )
            except Exception as e:
                # Record unexpected errors
                with lock:
                    results.append(
                        {
                            "thread_id": thread_id,
                            "success": False,
                            "error": str(e),
                            "quantity": quantity,
                        }
                    )

        # Create 5 threads, each trying to reserve 3 units
        threads = []
        num_threads = 5
        quantity_per_thread = 3

        for i in range(num_threads):
            thread = threading.Thread(
                target=reserve_stock_thread, args=(i, quantity_per_thread)
            )
            threads.append(thread)

        # Start all threads simultaneously
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify results
        assert len(results) == num_threads, "All threads should complete"

        # Count successes and failures
        successes = [r for r in results if r["success"]]
        failures = [r for r in results if not r["success"]]

        # Calculate total reserved quantity
        total_reserved = sum(r["quantity"] for r in successes)

        # Verify: At least one thread should fail (since 15 > 10)
        assert len(failures) >= 2, (
            f"Expected at least 2 failures when requesting {num_threads * quantity_per_thread} "
            f"units from stock of 10, but got {len(failures)} failures"
        )

        # Verify: Total reserved should not exceed available stock
        assert total_reserved <= 10, (
            f"Total reserved quantity ({total_reserved}) should not exceed "
            f"available stock (10)"
        )

        # Verify: Stock should never go negative
        product.refresh_from_db()
        assert product.stock >= 0, (
            f"Stock should never be negative, got {product.stock}"
        )

        # Verify: Stock should still be 10 (reservations don't decrement physical stock)
        assert product.stock == 10, (
            f"Physical stock should remain 10 (reservations don't decrement), "
            f"got {product.stock}"
        )

        # Verify: Available stock should be reduced by reserved quantity
        available = StockManager.get_available_stock(product.id)
        expected_available = 10 - total_reserved
        assert available == expected_available, (
            f"Available stock should be {expected_available}, got {available}"
        )

    def test_concurrent_decrement_stock_prevents_overselling(self):
        """
        threads trying to decrement 3 units each from product with stock=10.

        Expected behavior:
        - Total requested: 5 threads × 3 units = 15 units
        - Available stock: 10 units
        - At least 2 threads should fail (since 15 > 10)
        - Stock should never go negative
        - Final stock should be ≥ 0
        """
        # Create product with 10 units in stock
        product = ProductFactory(stock=10)

        # Track results from each thread
        results = []
        lock = threading.Lock()

        def decrement_stock_thread(thread_id: int, quantity: int):
            """
            Thread function to attempt stock decrement.

            Each thread tries to decrement stock and records success/failure.
            """
            # Each thread needs its own database connection
            connection.close()

            try:
                # Attempt to decrement stock
                StockManager.decrement_stock(
                    product_id=product.id,
                    quantity=quantity,
                    order_id=thread_id,  # Use thread_id as mock order_id
                    reason=f"Concurrent test thread {thread_id}",
                )

                # Record success
                with lock:
                    results.append(
                        {
                            "thread_id": thread_id,
                            "success": True,
                            "quantity": quantity,
                        }
                    )
            except InsufficientStockError:
                # Record failure (expected for some threads)
                with lock:
                    results.append(
                        {
                            "thread_id": thread_id,
                            "success": False,
                            "quantity": quantity,
                        }
                    )
            except Exception as e:
                # Record unexpected errors
                with lock:
                    results.append(
                        {
                            "thread_id": thread_id,
                            "success": False,
                            "error": str(e),
                            "quantity": quantity,
                        }
                    )

        # Create 5 threads, each trying to decrement 3 units
        threads = []
        num_threads = 5
        quantity_per_thread = 3

        for i in range(num_threads):
            thread = threading.Thread(
                target=decrement_stock_thread, args=(i, quantity_per_thread)
            )
            threads.append(thread)

        # Start all threads simultaneously
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify results
        assert len(results) == num_threads, "All threads should complete"

        # Count successes and failures
        successes = [r for r in results if r["success"]]
        failures = [r for r in results if not r["success"]]

        # Calculate total decremented quantity
        total_decremented = sum(r["quantity"] for r in successes)

        # Verify: At least one thread should fail (since 15 > 10)
        assert len(failures) >= 2, (
            f"Expected at least 2 failures when requesting {num_threads * quantity_per_thread} "
            f"units from stock of 10, but got {len(failures)} failures"
        )

        # Verify: Total decremented should not exceed initial stock
        assert total_decremented <= 10, (
            f"Total decremented quantity ({total_decremented}) should not exceed "
            f"initial stock (10)"
        )

        # Verify: Stock should never go negative
        product.refresh_from_db()
        assert product.stock >= 0, (
            f"Stock should never be negative, got {product.stock}"
        )

        # Verify: Final stock should equal initial stock minus total decremented
        expected_final_stock = 10 - total_decremented
        assert product.stock == expected_final_stock, (
            f"Final stock should be {expected_final_stock}, got {product.stock}"
        )

    def test_concurrent_order_creation_prevents_overselling(self):
        """
        Test: Multiple threads creating orders simultaneously from same product.

        This test simulates a realistic scenario where multiple customers
        attempt to purchase the same product at the same time through the
        complete order creation flow.

        Expected behavior:
        - Some orders should succeed
        - Some orders should fail with InsufficientStockError
        - Total quantity in successful orders should not exceed available stock
        - Stock should never go negative
        """
        # Create product with 10 units in stock
        product = ProductFactory(stock=10)

        # Create payment way for orders
        pay_way = PayWayFactory()

        # Track results from each thread
        results = []
        lock = threading.Lock()

        def create_order_thread(thread_id: int, quantity: int):
            """
            Thread function to attempt order creation.

            Each thread creates a cart, adds items, and attempts to create an order.
            """
            # Each thread needs its own database connection
            connection.close()

            try:
                # Create user for this thread
                user = UserAccountFactory(email=f"user{thread_id}@test.com")

                # Create cart with items
                cart = CartFactory(user=user)
                CartItemFactory(cart=cart, product=product, quantity=quantity)

                # Attempt to create order
                # Note: In real flow, payment_intent_id would be provided
                # For this test, we're testing the stock management aspect
                order = OrderService.create_order_from_cart(
                    cart=cart,
                    shipping_address={
                        "first_name": f"User{thread_id}",
                        "last_name": "Test",
                        "email": f"user{thread_id}@test.com",
                        "street": "Test Street",
                        "street_number": "123",
                        "city": "Test City",
                        "zipcode": "12345",
                        "country_id": 1,
                        "phone": "+1234567890",
                    },
                    payment_intent_id=f"pi_test_{thread_id}",
                    pay_way=pay_way,
                    user=user,
                )

                # Record success
                with lock:
                    results.append(
                        {
                            "thread_id": thread_id,
                            "success": True,
                            "order_id": order.id,
                            "quantity": quantity,
                        }
                    )
            except InsufficientStockError:
                # Record failure (expected for some threads)
                with lock:
                    results.append(
                        {
                            "thread_id": thread_id,
                            "success": False,
                            "quantity": quantity,
                        }
                    )
            except Exception as e:
                # Record unexpected errors
                with lock:
                    results.append(
                        {
                            "thread_id": thread_id,
                            "success": False,
                            "error": str(e),
                            "quantity": quantity,
                        }
                    )

        # Create 5 threads, each trying to order 3 units
        threads = []
        num_threads = 5
        quantity_per_thread = 3

        for i in range(num_threads):
            thread = threading.Thread(
                target=create_order_thread, args=(i, quantity_per_thread)
            )
            threads.append(thread)

        # Start all threads simultaneously
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify results
        assert len(results) == num_threads, "All threads should complete"

        # Count successes and failures
        successes = [r for r in results if r["success"]]
        failures = [r for r in results if not r["success"]]

        # Calculate total ordered quantity
        total_ordered = sum(r["quantity"] for r in successes)

        # Verify: At least one thread should fail (since 15 > 10)
        assert len(failures) >= 2, (
            f"Expected at least 2 failures when ordering {num_threads * quantity_per_thread} "
            f"units from stock of 10, but got {len(failures)} failures"
        )

        # Verify: Total ordered should not exceed initial stock
        assert total_ordered <= 10, (
            f"Total ordered quantity ({total_ordered}) should not exceed "
            f"initial stock (10)"
        )

        # Verify: Stock should never go negative
        product.refresh_from_db()
        assert product.stock >= 0, (
            f"Stock should never be negative, got {product.stock}"
        )

        # Verify: Final stock should equal initial stock minus total ordered
        expected_final_stock = 10 - total_ordered
        assert product.stock == expected_final_stock, (
            f"Final stock should be {expected_final_stock}, got {product.stock}"
        )

    def test_high_concurrency_stress_test(self):
        """
        Test: High concurrency stress test with many threads.

        This test simulates extreme concurrency with 20 threads attempting
        to reserve stock from a product with limited availability.

        Expected behavior:
        - System should handle high concurrency gracefully
        - No deadlocks or race conditions
        - Stock should never go negative
        - Total reserved should not exceed available stock
        """
        # Create product with 50 units in stock
        product = ProductFactory(stock=50)

        # Track results from each thread
        results = []
        lock = threading.Lock()

        def reserve_thread(thread_id: int, quantity: int):
            """Thread that attempts to reserve stock."""
            connection.close()
            try:
                StockManager.reserve_stock(
                    product_id=product.id,
                    quantity=quantity,
                    session_id=f"stress-{thread_id}",
                    user_id=None,
                )
                with lock:
                    results.append(
                        {
                            "thread_id": thread_id,
                            "success": True,
                            "quantity": quantity,
                        }
                    )
            except InsufficientStockError:
                with lock:
                    results.append(
                        {
                            "thread_id": thread_id,
                            "success": False,
                            "quantity": quantity,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append(
                        {
                            "thread_id": thread_id,
                            "success": False,
                            "error": str(e),
                        }
                    )

        # Create 20 threads, each trying to reserve 4 units
        # Total requested: 20 × 4 = 80 units
        # Available: 50 units
        # Expected: ~12-13 successes, ~7-8 failures
        threads = []
        num_threads = 20
        quantity_per_thread = 4

        for i in range(num_threads):
            thread = threading.Thread(
                target=reserve_thread, args=(i, quantity_per_thread)
            )
            threads.append(thread)

        # Start all threads simultaneously
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify results
        assert len(results) == num_threads, "All threads should complete"

        # Count successes and failures
        successes = [r for r in results if r["success"]]
        failures = [r for r in results if not r["success"]]

        # Calculate total reserved quantity
        total_reserved = sum(r["quantity"] for r in successes)

        # Verify: Some threads should fail (since 80 > 50)
        assert len(failures) > 0, (
            f"Expected some failures when requesting {num_threads * quantity_per_thread} "
            f"units from stock of 50, but all succeeded"
        )

        # Verify: Total reserved should not exceed available stock
        assert total_reserved <= 50, (
            f"Total reserved quantity ({total_reserved}) should not exceed "
            f"available stock (50)"
        )

        # Verify: Stock should never go negative
        product.refresh_from_db()
        assert product.stock >= 0, (
            f"Stock should never be negative, got {product.stock}"
        )

        # Verify: Stock should still be 50 (reservations don't decrement physical stock)
        assert product.stock == 50, (
            f"Physical stock should remain 50 (reservations don't decrement), "
            f"got {product.stock}"
        )

        # Verify: Available stock should be reduced by reserved quantity
        available = StockManager.get_available_stock(product.id)
        expected_available = 50 - total_reserved
        assert available == expected_available, (
            f"Available stock should be {expected_available}, got {available}"
        )

        # Verify: No unexpected errors occurred
        errors = [r for r in results if "error" in r]
        assert len(errors) == 0, (
            f"No unexpected errors should occur, but got: {errors}"
        )
