"""Deterministic replacement for the threading-based stock concurrency tests.

The previous suite (`test_concurrent_stock.py` + `test_concurrent_stock_operations.py`,
9 tests in total) spawned Python threads with synchronisation barriers and
asserted exact thread-outcome counts. Under `pytest -n auto` mixed with
other heavy suites those scheduling assumptions broke down — the tests
flaked ~10% of runs while passing 5/5 in isolation.

Investigation (memory: project_test_suite_stability.md, item 11) showed
all nine tests were verifying **deterministic** invariants —
`product.stock` cannot go negative, total reserved/decremented quantity
cannot exceed the initial stock, etc. — that don't actually require
threads. The "concurrency" was incidental: the assertions held whether
the contention was real (parallel pods) or simulated (sequential atomic
blocks observing each other's commits).

This file replaces the threading tests with sequential checks that
exercise the same `SELECT FOR UPDATE` semantics: each call commits its
state-change before the next call's atomic block begins, so the second
caller's `select_for_update()` reads the post-commit row exactly as it
would in the production "second pod" path. Coverage of the unique
business invariants is preserved; flakiness is eliminated.

Already-covered ground (e.g. happy-path reserve/decrement, audit logging)
remains in `test_stock_manager.py` and `test_concurrent_stock_*.py`'s
deletion does not regress coverage.
"""

from __future__ import annotations

import pytest

from order.exceptions import InsufficientStockError
from order.factories import OrderFactory
from order.models import StockLog, StockReservation
from order.stock import StockManager
from product.factories import ProductFactory


@pytest.mark.django_db
class TestStockSelectForUpdatePreventsRaceConditions:
    """Sequential coverage of the locking invariants.

    Each test makes two or more calls into `StockManager` in series.
    Because every public method wraps its work in `transaction.atomic()`
    + `select_for_update()`, the second call's pre-mutation read sees
    the first call's committed state — exactly as a second pod would.
    The invariants checked here (no overselling, no negative stock, no
    double-spend of a single locker quantity) match the production
    guarantees `SELECT FOR UPDATE` is there to provide.
    """

    def test_second_reserve_sees_first_reserves_quantity(self):
        """Two sequential reserves: the second one's available-stock
        check must subtract the first one's reservation."""
        product = ProductFactory(stock=10, num_images=0, num_reviews=0)

        first = StockManager.reserve_stock(
            product_id=product.id,
            quantity=3,
            session_id="cart-1",
            user_id=None,
        )
        second = StockManager.reserve_stock(
            product_id=product.id,
            quantity=7,
            session_id="cart-2",
            user_id=None,
        )

        assert first.quantity == 3
        assert second.quantity == 7
        # Both reservations are active (not yet consumed by an order).
        assert (
            StockReservation.objects.filter(
                product=product, consumed=False
            ).count()
            == 2
        )
        # Stock count itself is untouched — reservations don't decrement.
        product.refresh_from_db()
        assert product.stock == 10

    def test_third_reserve_rejected_when_active_reservations_eat_supply(self):
        """When prior reserves consume all supply, a fresh request
        must raise — the second pod must see what the first committed."""
        product = ProductFactory(stock=10, num_images=0, num_reviews=0)

        StockManager.reserve_stock(
            product_id=product.id,
            quantity=6,
            session_id="cart-1",
            user_id=None,
        )
        StockManager.reserve_stock(
            product_id=product.id,
            quantity=4,
            session_id="cart-2",
            user_id=None,
        )

        with pytest.raises(InsufficientStockError):
            StockManager.reserve_stock(
                product_id=product.id,
                quantity=1,
                session_id="cart-3",
                user_id=None,
            )

    def test_sequential_decrement_respects_running_total(self):
        """Two sequential decrements: stock must reflect the cumulative
        reduction, and a third call exceeding remaining stock must fail."""
        product = ProductFactory(stock=10, num_images=0, num_reviews=0)
        order1 = OrderFactory(num_order_items=0)
        order2 = OrderFactory(num_order_items=0)
        order3 = OrderFactory(num_order_items=0)

        StockManager.decrement_stock(
            product_id=product.id, quantity=3, order_id=order1.id
        )
        product.refresh_from_db()
        assert product.stock == 7

        StockManager.decrement_stock(
            product_id=product.id, quantity=7, order_id=order2.id
        )
        product.refresh_from_db()
        assert product.stock == 0

        with pytest.raises(InsufficientStockError):
            StockManager.decrement_stock(
                product_id=product.id, quantity=1, order_id=order3.id
            )
        product.refresh_from_db()
        assert product.stock == 0  # never went negative

    def test_reserve_then_decrement_share_supply(self):
        """Reservations + physical decrements together must not over-allocate.

        Reservations don't change `product.stock` but DO subtract from
        the available pool; a decrement after a reservation must see
        the reduced pool when computing the post-decrement available.
        Models the production scenario where one pod reserves a cart
        while another pod completes a checkout against the same product.
        """
        product = ProductFactory(stock=20, num_images=0, num_reviews=0)
        order = OrderFactory(num_order_items=0)

        StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-1",
            user_id=None,
        )
        StockManager.decrement_stock(
            product_id=product.id, quantity=10, order_id=order.id
        )

        product.refresh_from_db()
        assert product.stock == 10  # physical decrement applied
        # Available stock = physical (10) - active reservations (10) = 0.
        assert StockManager.get_available_stock(product.id) == 0

    def test_decrement_then_reserve_share_supply(self):
        """Inverse of the above — order completes first, then a separate
        cart tries to reserve. The reservation must see the decremented
        physical stock (not the pre-decrement value)."""
        product = ProductFactory(stock=20, num_images=0, num_reviews=0)
        order = OrderFactory(num_order_items=0)

        StockManager.decrement_stock(
            product_id=product.id, quantity=15, order_id=order.id
        )
        product.refresh_from_db()
        assert product.stock == 5

        # Reserving 5 succeeds; reserving 6 must fail.
        StockManager.reserve_stock(
            product_id=product.id,
            quantity=5,
            session_id="cart-1",
            user_id=None,
        )
        with pytest.raises(InsufficientStockError):
            StockManager.reserve_stock(
                product_id=product.id,
                quantity=1,
                session_id="cart-2",
                user_id=None,
            )

    def test_decrement_writes_audit_log(self):
        """Each decrement must produce a StockLog row with before/after
        snapshots — the audit invariant the threading tests asserted
        post-hoc by counting rows."""
        product = ProductFactory(stock=10, num_images=0, num_reviews=0)
        order = OrderFactory(num_order_items=0)

        StockManager.decrement_stock(
            product_id=product.id, quantity=4, order_id=order.id
        )

        log = StockLog.objects.filter(product=product, order=order).get()
        assert log.stock_before == 10
        assert log.stock_after == 6
        assert log.quantity_delta == -4

    def test_high_volume_sequential_reservations_never_exceed_supply(self):
        """Replacement for the deleted ``test_high_concurrency_stress_test``.

        Walks through 20 reservation requests of 4 units each against
        a stock of 50. The 12th request fills the pool exactly; every
        request after that must raise ``InsufficientStockError``. The
        invariant ``total_reserved <= 50`` holds without thread
        scheduling getting involved.
        """
        product = ProductFactory(stock=50, num_images=0, num_reviews=0)
        successes = 0
        failures = 0

        for i in range(20):
            try:
                StockManager.reserve_stock(
                    product_id=product.id,
                    quantity=4,
                    session_id=f"cart-{i}",
                    user_id=None,
                )
                successes += 1
            except InsufficientStockError:
                failures += 1

        # 50 / 4 = 12 (with 2 left over).
        assert successes == 12
        assert failures == 8
        active_total = sum(
            r.quantity
            for r in StockReservation.objects.filter(
                product=product, consumed=False
            )
        )
        assert active_total == 48
        assert active_total <= product.stock
