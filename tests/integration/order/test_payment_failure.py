from datetime import timedelta

import pytest

from django.utils import timezone

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.services import OrderService
from order.stock import StockManager
from order.tasks import auto_cancel_stuck_pending_orders
from product.factories import ProductFactory


@pytest.mark.django_db
class TestPaymentFailureKeepsStockDuringRetryWindow:
    """
    ``OrderService.handle_payment_failed`` only flips ``payment_status``
    to FAILED — it deliberately does NOT touch stock.

    Stock is consumed at order-creation time (``_consume_stock_for_order``,
    both the payment-first and offline paths), not on webhook receipt, so
    by the time a ``payment_intent.payment_failed`` webhook arrives the
    stock is already "sold". It must stay that way through a retry grace
    window: Stripe allows retrying a failed PaymentIntent with a new
    card, this storefront's back-to-form retry flow is live, and
    ``handle_payment_succeeded`` never re-consumes stock on a later
    success. Releasing stock the moment a payment fails would let a
    retried, successful payment ship an order against inventory that was
    already handed back to another shopper — a real oversell.

    The terminal release path is ``order.tasks.auto_cancel_stuck_pending_
    orders``, which cancels PENDING orders whose payment has been FAILED
    for longer than ``ORDER_AUTO_CANCEL_FAILED_PAYMENT_MINUTES`` (default
    30 minutes) via ``OrderService.cancel_order`` — see
    ``TestAutoCancelReleasesStockAfterGracePeriod`` below.
    """

    def setup_method(self):
        from unittest.mock import patch

        # Patch the TTL to ensure reservations don't expire during slow
        # test executions.
        self.patcher = patch(
            "order.stock.StockManager.get_reservation_ttl_minutes",
            return_value=60,
        )
        self.patcher.start()

    def teardown_method(self):
        self.patcher.stop()

    def test_payment_failure_does_not_release_linked_reservations(self):
        """A FAILED payment must leave any reservation linked via
        ``metadata['stock_reservation_ids']`` untouched — releasing it
        would free stock a retried payment might still need."""
        product = ProductFactory(stock=100)
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-retry",
            user_id=None,
        )
        payment_id = "pi_test_retry_keeps_reservation"
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            num_order_items=0,
            metadata={"stock_reservation_ids": [reservation.id]},
        )

        result = OrderService.handle_payment_failed(payment_id)

        assert result is not None
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.FAILED

        reservation.refresh_from_db()
        assert not reservation.consumed, (
            "handle_payment_failed must not release the reservation — "
            "stock stays held through the retry grace window"
        )
        assert StockManager.get_available_stock(product.id) == 90

    def test_payment_failure_does_not_restore_physical_stock(self):
        """Real order items (stock already decremented at order-creation
        time) must not be restored on payment failure — only the
        terminal auto-cancel path restores physical stock."""
        product = ProductFactory(stock=100)
        payment_id = "pi_test_retry_keeps_physical_stock"
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            num_order_items=0,
        )
        # Mirror the checkout-time decrement `_consume_stock_for_order`
        # performs before the payment webhook ever arrives.
        StockManager.decrement_stock(
            product_id=product.id,
            quantity=10,
            order_id=order.id,
            reason="test_setup: simulate checkout decrement",
        )
        order.items.create(product=product, price=product.price, quantity=10)
        product.refresh_from_db()
        assert product.stock == 90

        OrderService.handle_payment_failed(payment_id)

        product.refresh_from_db()
        assert product.stock == 90, (
            "physical stock must stay decremented after a payment "
            "failure — it is only restored by the terminal cancel path"
        )

    @pytest.mark.parametrize(
        "order_status,payment_status,should_update",
        [
            # PENDING orders should be updated
            (OrderStatus.PENDING, PaymentStatus.PENDING, True),
            (OrderStatus.PENDING, PaymentStatus.PROCESSING, True),
            # Already failed - idempotent (FAILED is not a settled state)
            (OrderStatus.PENDING, PaymentStatus.FAILED, True),
            # COMPLETED is a settled state — stale payment_failed must NOT
            # regress the payment_status back to FAILED.
            (OrderStatus.PROCESSING, PaymentStatus.COMPLETED, False),
            (OrderStatus.SHIPPED, PaymentStatus.COMPLETED, False),
            # Canceled orders with PENDING payment_status — still writable
            (OrderStatus.CANCELED, PaymentStatus.PENDING, True),
            # REFUNDED is a settled state — stale payment_failed must NOT
            # regress the payment_status back to FAILED.
            (OrderStatus.REFUNDED, PaymentStatus.REFUNDED, False),
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

        When should_update is True the handler writes FAILED.
        When should_update is False the order is already in a settled
        payment state (COMPLETED / REFUNDED / PARTIALLY_REFUNDED /
        CANCELED) and a stale out-of-order webhook must NOT regress it.
        Stripe does NOT guarantee webhook event delivery order.
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

        if should_update:
            # Non-settled start state: handler writes FAILED
            assert order.payment_status == PaymentStatus.FAILED, (
                f"Payment status should be FAILED for {order_status}/"
                f"{payment_status}, got {order.payment_status}"
            )
        else:
            # Settled start state: handler must return the order unchanged
            assert order.payment_status == payment_status, (
                f"Settled payment_status {payment_status} must not be "
                f"regressed to FAILED for order_status={order_status}, "
                f"got {order.payment_status}"
            )

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
        Calling handle_payment_failed multiple times is idempotent on the
        status write. Since the handler has no stock side effects at all,
        there is nothing to double-apply — the reservation stays exactly
        as it was before the first call.
        """
        product = ProductFactory(stock=100)
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-idempotent",
            user_id=None,
        )
        payment_id = "pi_test_idempotent"
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            num_order_items=0,
            metadata={"stock_reservation_ids": [reservation.id]},
        )

        result1 = OrderService.handle_payment_failed(payment_id)
        result2 = OrderService.handle_payment_failed(payment_id)
        result3 = OrderService.handle_payment_failed(payment_id)

        assert result1 is not None
        assert result2 is not None
        assert result3 is not None

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.FAILED

        reservation.refresh_from_db()
        assert not reservation.consumed, (
            "repeated failure notifications must not release the "
            "reservation either"
        )


@pytest.mark.django_db
class TestAutoCancelReleasesStockAfterGracePeriod:
    """
    ``order.tasks.auto_cancel_stuck_pending_orders`` is the ONLY place a
    failed-payment order's stock gets released. It cancels PENDING
    orders whose payment has been FAILED for longer than
    ``ORDER_AUTO_CANCEL_FAILED_PAYMENT_MINUTES`` (default 30 minutes) via
    ``OrderService.cancel_order``, which restores stock through
    ``StockManager.increment_stock`` on each order item and releases any
    reservation still linked via ``metadata['stock_reservation_ids']``.
    """

    def _create_stuck_order(self, *, product, quantity, minutes_old):
        payment_id = f"pi_test_stuck_{product.id}_{minutes_old}"
        order = Order.objects.create(
            user=None,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.FAILED,
            payment_id=payment_id,
            email="stuck@example.com",
            first_name="Stuck",
            last_name="Order",
            phone="+1234567890",
            street="123 Main St",
            street_number="1",
            city="New York",
            zipcode="10001",
        )
        # Mirror the checkout-time decrement `_consume_stock_for_order`
        # performs — by the time an order exists, its items' stock is
        # already physically decremented, not merely reserved.
        StockManager.decrement_stock(
            product_id=product.id,
            quantity=quantity,
            order_id=order.id,
            reason="test_setup: simulate checkout decrement",
        )
        order.items.create(
            product=product, price=product.price, quantity=quantity
        )
        # `updated_at` is `auto_now=True` — bypass `save()` to backdate it
        # past the grace-window cutoff without touching anything else.
        Order.objects.filter(pk=order.pk).update(
            updated_at=timezone.now() - timedelta(minutes=minutes_old)
        )
        order.refresh_from_db()
        return order

    def test_stock_restored_and_order_canceled_past_grace_window(self):
        product = ProductFactory(stock=100)
        order = self._create_stuck_order(
            product=product, quantity=10, minutes_old=31
        )
        product.refresh_from_db()
        assert product.stock == 90, (
            "sanity: setup decremented stock the way checkout does"
        )

        result = auto_cancel_stuck_pending_orders()

        assert result["canceled_failed"] == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        product.refresh_from_db()
        assert product.stock == 100, (
            "stock restored exactly once via cancel_order"
        )

    def test_running_task_twice_does_not_double_restore_stock(self):
        product = ProductFactory(stock=100)
        order = self._create_stuck_order(
            product=product, quantity=10, minutes_old=31
        )

        auto_cancel_stuck_pending_orders()
        second_result = auto_cancel_stuck_pending_orders()

        assert second_result["canceled_failed"] == 0, (
            "the order is no longer PENDING after the first cancel, so "
            "the second run must not touch it again"
        )
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        product.refresh_from_db()
        assert product.stock == 100, "second run must not double-restore"

    def test_grace_window_not_yet_elapsed_leaves_order_and_stock_untouched(
        self,
    ):
        product = ProductFactory(stock=100)
        order = self._create_stuck_order(
            product=product, quantity=10, minutes_old=10
        )

        result = auto_cancel_stuck_pending_orders()

        assert result["canceled_failed"] == 0
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING, (
            "an order inside the retry grace window must not be canceled"
        )
        product.refresh_from_db()
        assert product.stock == 90, (
            "stock must stay held during the grace window"
        )

    def test_reservation_linked_via_metadata_is_released_on_cancel(self):
        """A reservation that was never converted to a sale (e.g. it
        overshot the cart and was left dangling) is released as part of
        the same terminal cancel, alongside the item-based stock
        restoration."""
        product = ProductFactory(stock=100)
        from unittest.mock import patch

        with patch(
            "order.stock.StockManager.get_reservation_ttl_minutes",
            return_value=60,
        ):
            reservation = StockManager.reserve_stock(
                product_id=product.id,
                quantity=5,
                session_id="cart-stuck-reservation",
                user_id=None,
            )

        order = self._create_stuck_order(
            product=product, quantity=10, minutes_old=31
        )
        Order.objects.filter(pk=order.pk).update(
            metadata={"stock_reservation_ids": [reservation.id]}
        )

        auto_cancel_stuck_pending_orders()

        reservation.refresh_from_db()
        assert reservation.consumed, (
            "a still-open reservation linked to the order must be "
            "released as part of the terminal cancel"
        )
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
