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

    def test_order_factory_with_items(self):
        with mock.patch(
            "order.factories.item.OrderItemFactory.create_batch"
        ) as mock_create_batch:
            order = OrderFactory.create(num_order_items=3)
            mock_create_batch.assert_called_once_with(3, order=order)

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

    def test_order_history_status_change(self):
        with mock.patch("order.models.history.OrderHistory.log_note"):
            order = OrderFactory.create()

        with mock.patch(
            "order.factories.history.OrderHistoryFactory.set_change_type_specific_data"
        ):
            history = OrderHistoryFactory.create_status_change(
                order=order,
                old_status=OrderStatus.PENDING,
                new_status=OrderStatus.PROCESSING,
            )

            history.previous_value = {"status": OrderStatus.PENDING}
            history.new_value = {"status": OrderStatus.PROCESSING}
            history.description = f"Status changed from {OrderStatus.PENDING} to {OrderStatus.PROCESSING}"
            history.save()

        self.assertEqual(history.change_type, "STATUS")
        self.assertEqual(
            history.previous_value.get("status"), OrderStatus.PENDING
        )
        self.assertEqual(
            history.new_value.get("status"), OrderStatus.PROCESSING
        )

    def test_order_item_history_factory(self):
        with mock.patch("order.models.history.OrderHistory.log_note"):
            order = OrderFactory.create()
            item = OrderItemFactory.create(order=order)

        history = OrderItemHistoryFactory.create(order_item=item)

        self.assertIsInstance(history, OrderItemHistory)
        self.assertIsNotNone(history.id)
        self.assertIsNotNone(history.order_item)

    def test_order_item_history_quantity_change(self):
        with mock.patch("order.models.history.OrderHistory.log_note"):
            order = OrderFactory.create()
            item = OrderItemFactory.create(order=order)

        with mock.patch(
            "order.factories.history.OrderItemHistoryFactory.set_change_type_specific_data"
        ):
            history = OrderItemHistoryFactory.create_quantity_change(
                order_item=item, old_quantity=2, new_quantity=5
            )

            history.previous_value = {"quantity": 2}
            history.new_value = {"quantity": 5}
            history.description = "Quantity changed from 2 to 5"
            history.save()

        self.assertEqual(history.change_type, "QUANTITY")
        self.assertEqual(history.previous_value.get("quantity"), 2)
        self.assertEqual(history.new_value.get("quantity"), 5)

    @mock.patch("order.models.history.OrderHistory.log_note")
    def test_creating_multiple_factories(self, mock_log_note):
        with mock.patch(
            "order.factories.item.OrderItemFactory.create_batch_for_order"
        ):
            order = OrderFactory.create_completed_order()

            OrderItem.objects.filter(order=order).delete()
            item1 = OrderItemFactory.create(order=order)
            item2 = OrderItemFactory.create(order=order)

            histories = OrderHistoryFactory.create_for_order(order, count=3)

            item1_histories = OrderItemHistoryFactory.create_for_order_item(
                item1, count=2
            )
            item2_histories = OrderItemHistoryFactory.create_for_order_item(
                item2, count=2
            )

            self.assertGreaterEqual(order.items.count(), 1)
            self.assertEqual(len(histories), 3)
            self.assertEqual(len(item1_histories), 2)
            self.assertEqual(len(item2_histories), 2)

    def test_factory_method_arguments(self):
        with mock.patch(
            "order.factories.order.OrderFactory.create_with_consistent_status_data"
        ) as mock_method:
            OrderFactory.create_shipped_order(test_param=123)

            mock_method.assert_called_once()
            args, kwargs = mock_method.call_args
            self.assertEqual(kwargs.get("status"), OrderStatus.SHIPPED)
            self.assertEqual(kwargs.get("test_param"), 123)

        with mock.patch(
            "order.factories.order.OrderFactory.create_with_consistent_status_data"
        ) as mock_method:
            OrderFactory.create_pending_order(test_param=456)

            mock_method.assert_called_once()
            args, kwargs = mock_method.call_args
            self.assertEqual(kwargs.get("status"), OrderStatus.PENDING)
            self.assertEqual(
                kwargs.get("payment_status"), PaymentStatus.PENDING
            )
            self.assertEqual(kwargs.get("test_param"), 456)

        with mock.patch(
            "order.factories.order.OrderFactory.create_with_consistent_status_data"
        ) as mock_method:
            OrderFactory.create_completed_order()

            mock_method.assert_called_once()
            args, kwargs = mock_method.call_args
            self.assertEqual(kwargs.get("status"), OrderStatus.COMPLETED)

        with mock.patch(
            "order.factories.order.OrderFactory.create_with_consistent_status_data"
        ) as mock_method:
            mock_order = mock.MagicMock()
            mock_order.items.exists.return_value = False
            mock_method.return_value = mock_order

            OrderFactory.create_refunded_order()

            mock_method.assert_called_once()
            args, kwargs = mock_method.call_args
            self.assertEqual(kwargs.get("status"), OrderStatus.REFUNDED)
            self.assertEqual(
                kwargs.get("payment_status"), PaymentStatus.REFUNDED
            )
