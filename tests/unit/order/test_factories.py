from unittest import mock

from django.test import TestCase

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import (
    OrderFactory,
    OrderHistoryFactory,
    OrderItemFactory,
    OrderItemHistoryFactory,
)
from order.models.history import OrderHistory, OrderItemHistory
from order.models.item import OrderItem
from order.models.order import Order


class TestOrderFactories(TestCase):
    def test_order_factory(self):
        order = OrderFactory.create()

        self.assertIsInstance(order, Order)
        self.assertIsNotNone(order.id)
        self.assertIsNotNone(order.email)
        self.assertIsNotNone(order.first_name)
        self.assertIsNotNone(order.last_name)

    def test_order_factory_with_specific_status(self):
        order = OrderFactory.create(status=OrderStatus.PROCESSING)

        self.assertEqual(order.status, OrderStatus.PROCESSING)

    def test_create_shipped_order(self):
        order = OrderFactory.create_shipped_order()

        self.assertEqual(order.status, OrderStatus.SHIPPED)
        self.assertIsNotNone(order.tracking_number)
        self.assertIsNotNone(order.shipping_carrier)
        self.assertEqual(order.payment_status, PaymentStatus.COMPLETED)

    def test_create_refunded_order(self):
        order = OrderFactory.create_refunded_order()

        self.assertEqual(order.status, OrderStatus.REFUNDED)
        self.assertEqual(order.payment_status, PaymentStatus.REFUNDED)

        refunded_items = sum(
            1 for item in order.items.all() if item.refunded_quantity > 0
        )
        self.assertGreater(refunded_items, 0)

    def test_order_item_factory(self):
        item = OrderItemFactory.create()

        self.assertIsInstance(item, OrderItem)
        self.assertIsNotNone(item.id)
        self.assertIsNotNone(item.price)
        self.assertGreater(item.quantity, 0)

    def test_order_item_with_refund(self):
        item = OrderItemFactory.create_with_refund()

        self.assertIsNotNone(item.id)
        self.assertGreater(item.refunded_quantity, 0)

    def test_order_history_factory(self):
        with mock.patch("order.models.history.OrderHistory.log_note"):
            order = OrderFactory.create()

        history = OrderHistoryFactory.create(order=order)

        self.assertIsInstance(history, OrderHistory)
        self.assertIsNotNone(history.id)
        self.assertIsNotNone(history.order)
        self.assertIsNotNone(history.user_agent)

    def test_order_item_history_factory(self):
        with mock.patch("order.models.history.OrderHistory.log_note"):
            order = OrderFactory.create()
            item = OrderItemFactory.create(order=order)

        history = OrderItemHistoryFactory.create(order_item=item)

        self.assertIsInstance(history, OrderItemHistory)
        self.assertIsNotNone(history.id)
        self.assertIsNotNone(history.order_item)

    def test_order_factory_builds_the_requested_items(self):
        order = OrderFactory.create(num_order_items=3)

        self.assertEqual(order.items.count(), 3)

    def test_a_status_change_keeps_the_requested_statuses(self):
        history = OrderHistoryFactory.create_status_change(
            order=OrderFactory.create(num_order_items=0),
            old_status=OrderStatus.PENDING,
            new_status=OrderStatus.PROCESSING,
        )
        history.refresh_from_db()

        self.assertEqual(history.change_type, "STATUS")
        self.assertEqual(history.previous_value, {"status": "PENDING"})
        self.assertEqual(history.new_value, {"status": "PROCESSING"})
        self.assertEqual(
            history.safe_translation_getter("description"),
            "Status changed from PENDING to PROCESSING",
        )

    def test_a_quantity_change_keeps_the_requested_quantities(self):
        item = OrderItemFactory.create(
            order=OrderFactory.create(num_order_items=0)
        )

        history = OrderItemHistoryFactory.create_quantity_change(
            order_item=item, old_quantity=2, new_quantity=5
        )
        history.refresh_from_db()

        self.assertEqual(history.previous_value, {"quantity": 2})
        self.assertEqual(history.new_value, {"quantity": 5})
        self.assertEqual(
            history.safe_translation_getter("description"),
            "Quantity changed from 2 to 5",
        )

    def test_explicit_values_win_over_the_generated_ones(self):
        history = OrderHistoryFactory.create(
            order=OrderFactory.create(num_order_items=0),
            change_type="PAYMENT",
            previous_value={"payment_status": "PENDING"},
            new_value={"payment_status": "COMPLETED"},
        )
        history.refresh_from_db()

        self.assertEqual(history.new_value, {"payment_status": "COMPLETED"})
