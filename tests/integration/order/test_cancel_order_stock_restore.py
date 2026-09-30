"""``OrderService.cancel_order`` restores the stock an order HOLDS.

The restore sums the order's own ``StockLog`` movements rather than its
listed quantities, so an item that never took stock gets none back and a
second cancel restores nothing. ``StockManager.adjust_stock`` must log
only what it moved for that sum to stay true.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from order.enum.status import OrderStatus
from order.services import OrderService

pytestmark = pytest.mark.assert_english


@pytest.mark.django_db
class TestCancelOrderWithStockManager:
    """
    Test suite for updated cancel_order method using StockManager.
    """

    def test_cancel_does_not_invent_stock_an_order_never_took(self):
        """An OrderItem created outside checkout never decremented stock.

        ``handle_order_item_post_save`` deliberately skips the decrement on
        create so the checkout path is not double-counted, and the
        standalone OrderItem admin exposes order/product/quantity as
        editable. Restoring per listed item therefore put units on the
        shelf that were never taken off it, and the store oversold them.
        """
        from order.factories import OrderFactory
        from order.models import OrderItem
        from product.factories import ProductFactory

        product = ProductFactory(stock=10)
        order = OrderFactory(status=OrderStatus.PENDING, num_order_items=0)
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=3,
            price=product.price,
            sort_order=1,
        )
        product.refresh_from_db()
        assert product.stock == 10

        OrderService.cancel_order(
            order=order, reason="Customer request", refund_payment=False
        )

        product.refresh_from_db()
        assert product.stock == 10

    def test_cancel_restores_only_the_part_still_held(self):
        """A line whose stock was taken and a line whose stock was not, on
        one order: only the first comes back."""
        from order.factories import OrderFactory
        from order.models import OrderItem
        from order.stock import StockManager
        from product.factories import ProductFactory

        taken = ProductFactory(stock=10)
        untaken = ProductFactory(stock=10)
        order = OrderFactory(status=OrderStatus.PENDING, num_order_items=0)
        for product in (taken, untaken):
            OrderItem.objects.create(
                order=order,
                product=product,
                quantity=2,
                price=product.price,
                sort_order=1,
            )
        StockManager.decrement_stock(
            product_id=taken.id,
            quantity=2,
            order_id=order.id,
            reason="test: order created",
        )

        OrderService.cancel_order(
            order=order, reason="Customer request", refund_payment=False
        )

        taken.refresh_from_db()
        untaken.refresh_from_db()
        assert taken.stock == 10
        assert untaken.stock == 10

    def test_cancelling_twice_restores_once(self):
        """The restore is derived from the order's own net movement, so a
        second pass finds nothing outstanding."""
        from order.factories import OrderFactory
        from order.models import OrderItem
        from order.stock import StockManager
        from product.factories import ProductFactory

        product = ProductFactory(stock=10)
        order = OrderFactory(status=OrderStatus.PENDING, num_order_items=0)
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=4,
            price=product.price,
            sort_order=1,
        )
        StockManager.decrement_stock(
            product_id=product.id,
            quantity=4,
            order_id=order.id,
            reason="test: order created",
        )

        OrderService.cancel_order(
            order=order, reason="first", refund_payment=False
        )
        product.refresh_from_db()
        assert product.stock == 10

        order.status = OrderStatus.PENDING
        order.save(update_fields=["status"])
        OrderService.cancel_order(
            order=order, reason="second", refund_payment=False
        )

        product.refresh_from_db()
        assert product.stock == 10

    def test_cancel_gives_back_what_a_converted_reservation_took(self):
        """A checkout's reservation becomes the sale; cancelling returns
        those units, and the (already consumed) reservation id in the
        metadata does not make the cancel fail."""
        from order.factories import OrderFactory
        from order.models import OrderItem, StockReservation
        from order.stock import StockManager
        from product.factories import ProductFactory

        product = ProductFactory(stock=5)
        order = OrderFactory(status=OrderStatus.PENDING, num_order_items=0)
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=3,
            price=product.price,
            sort_order=1,
        )
        reservation = StockReservation.objects.create(
            product=product,
            quantity=3,
            session_id="test-session",
            expires_at=timezone.now() + timedelta(minutes=15),
            order=order,
        )
        order.metadata = {"stock_reservation_ids": [reservation.id]}
        order.save()
        StockManager.convert_reservation_to_sale(
            reservation_id=reservation.id, order_id=order.id
        )
        product.refresh_from_db()
        assert product.stock == 2

        canceled_order, _refund_info = OrderService.cancel_order(
            order=order, reason="Customer request", refund_payment=False
        )

        assert canceled_order.status == OrderStatus.CANCELED
        product.refresh_from_db()
        assert product.stock == 5


@pytest.mark.django_db
class TestAdjustStockAuditRow:
    """``StockLog`` is read back as fact — ``cancel_order`` restores an
    order by summing its rows — so a row must record what MOVED."""

    def test_a_floored_decrement_logs_only_what_moved(self):
        from order.models import StockLog
        from order.stock import StockManager
        from product.factories import ProductFactory

        product = ProductFactory(stock=2)

        StockManager.adjust_stock(
            product=product, delta=-5, reason="oversized correction"
        )

        product.refresh_from_db()
        log = StockLog.objects.filter(product=product).latest("id")
        assert product.stock == 0
        assert log.quantity_delta == -2, (
            "logging the requested -5 would let a later restore invent "
            "three units that never left the shelf"
        )
        assert log.stock_before + log.quantity_delta == log.stock_after
        assert "floored" in log.reason

    def test_an_unfloored_adjustment_is_logged_verbatim(self):
        from order.models import StockLog
        from order.stock import StockManager
        from product.factories import ProductFactory

        product = ProductFactory(stock=10)

        StockManager.adjust_stock(
            product=product, delta=-3, reason="admin order item edit"
        )

        log = StockLog.objects.filter(product=product).latest("id")
        assert log.quantity_delta == -3
        assert log.reason == "admin order item edit"
        assert log.stock_before + log.quantity_delta == log.stock_after

    def test_a_floored_decrement_cannot_inflate_stock_on_cancel(self):
        """The two defects compose: an over-large admin edit followed by
        a cancellation."""
        from order.factories import OrderFactory
        from order.models import OrderItem
        from order.stock import StockManager
        from product.factories import ProductFactory

        product = ProductFactory(stock=2)
        order = OrderFactory(status=OrderStatus.PENDING, num_order_items=0)
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=5,
            price=product.price,
            sort_order=1,
        )
        StockManager.adjust_stock(
            product=product,
            delta=-5,
            reason="admin order item edit",
            order_id=order.id,
        )
        product.refresh_from_db()
        assert product.stock == 0

        OrderService.cancel_order(
            order=order, reason="Customer request", refund_payment=False
        )

        product.refresh_from_db()
        assert product.stock == 2, "cancel restored stock that never moved"
