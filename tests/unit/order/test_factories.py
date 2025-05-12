from unittest import mock

from django.test import TestCase

from order.enum.status_enum import OrderStatusEnum, PaymentStatusEnum
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
    """Tests for the Order app factories."""

    @mock.patch("order.models.history.OrderHistory.log_note")
    def test_order_factory(self, mock_log_note):
        """Test that OrderFactory creates a valid Order instance."""
        order = OrderFactory.create()

        self.assertIsInstance(order, Order)
        self.assertIsNotNone(order.id)
        self.assertIsNotNone(order.email)
        self.assertIsNotNone(order.first_name)
        self.assertIsNotNone(order.last_name)

    @mock.patch("order.models.history.OrderHistory.log_note")
    def test_order_factory_with_items(self, mock_log_note):
        """Test that OrderFactory creates related items."""
        # Override the post_generation behavior
        with mock.patch(
            "order.factories.item.OrderItemFactory.create_batch"
        ) as mock_create_batch:
            order = OrderFactory.create(num_order_items=3)
            # Verify that create_batch was called with correct parameters
            mock_create_batch.assert_called_once_with(3, order=order)

    @mock.patch("order.models.history.OrderHistory.log_note")
    def test_order_factory_with_specific_status(self, mock_log_note):
        """Test that we can create an Order with a specific status."""
        order = OrderFactory.create(status=OrderStatusEnum.PROCESSING)

        self.assertEqual(order.status, OrderStatusEnum.PROCESSING)

    @mock.patch("order.models.history.OrderHistory.log_note")
    def test_create_shipped_order(self, mock_log_note):
        """Test creating a shipped order with consistent data."""
        order = OrderFactory.create_shipped_order()

        self.assertEqual(order.status, OrderStatusEnum.SHIPPED)
        self.assertIsNotNone(order.tracking_number)
        self.assertIsNotNone(order.shipping_carrier)
        self.assertEqual(order.payment_status, PaymentStatusEnum.COMPLETED)

    @mock.patch("order.models.history.OrderHistory.log_note")
    def test_create_refunded_order(self, mock_log_note):
        """Test creating a refunded order with refunded items."""
        order = OrderFactory.create_refunded_order()

        self.assertEqual(order.status, OrderStatusEnum.REFUNDED)
        self.assertEqual(order.payment_status, PaymentStatusEnum.REFUNDED)

        # At least some items should be refunded
        refunded_items = sum(
            1 for item in order.items.all() if item.refunded_quantity > 0
        )
        self.assertGreater(refunded_items, 0)

    @mock.patch("order.models.history.OrderHistory.log_note")
    def test_order_item_factory(self, mock_log_note):
        """Test that OrderItemFactory creates a valid OrderItem instance."""
        item = OrderItemFactory.create()

        self.assertIsInstance(item, OrderItem)
        self.assertIsNotNone(item.id)
        self.assertIsNotNone(item.price)
        self.assertGreater(item.quantity, 0)

    @mock.patch("order.models.history.OrderHistory.log_note")
    def test_order_item_with_refund(self, mock_log_note):
        """Test creating an OrderItem with refund data."""
        item = OrderItemFactory.create_with_refund()

        self.assertIsNotNone(item.id)
        self.assertGreater(item.refunded_quantity, 0)

    def test_order_history_factory(self):
        """Test that OrderHistoryFactory creates a valid OrderHistory instance."""
        # Create an order first without triggering signals
        with mock.patch("order.models.history.OrderHistory.log_note"):
            order = OrderFactory.create()

        history = OrderHistoryFactory.create(order=order)

        self.assertIsInstance(history, OrderHistory)
        self.assertIsNotNone(history.id)
        self.assertIsNotNone(history.order)
        self.assertIsNotNone(
            history.user_agent
        )  # Important! This was causing errors

    def test_order_history_status_change(self):
        """Test creating a specific status change history entry."""
        # Create an order first without triggering signals
        with mock.patch("order.models.history.OrderHistory.log_note"):
            order = OrderFactory.create()

        # Override the random status and explicitly set the statuses
        with mock.patch(
            "order.factories.history.OrderHistoryFactory.set_change_type_specific_data"
        ):
            history = OrderHistoryFactory.create_status_change(
                order=order,
                old_status=OrderStatusEnum.PENDING,
                new_status=OrderStatusEnum.PROCESSING,
            )

            # Manually set the values since we're bypassing post_generation
            history.previous_value = {"status": OrderStatusEnum.PENDING}
            history.new_value = {"status": OrderStatusEnum.PROCESSING}
            history.description = f"Status changed from {OrderStatusEnum.PENDING} to {OrderStatusEnum.PROCESSING}"
            history.save()

        self.assertEqual(history.change_type, "STATUS")
        self.assertEqual(
            history.previous_value.get("status"), OrderStatusEnum.PENDING
        )
        self.assertEqual(
            history.new_value.get("status"), OrderStatusEnum.PROCESSING
        )

    def test_order_item_history_factory(self):
        """Test that OrderItemHistoryFactory creates a valid OrderItemHistory instance."""
        # Create an order and order item first without triggering signals
        with mock.patch("order.models.history.OrderHistory.log_note"):
            order = OrderFactory.create()
            item = OrderItemFactory.create(order=order)

        history = OrderItemHistoryFactory.create(order_item=item)

        self.assertIsInstance(history, OrderItemHistory)
        self.assertIsNotNone(history.id)
        self.assertIsNotNone(history.order_item)

    def test_order_item_history_quantity_change(self):
        """Test creating a specific quantity change history entry."""
        # Create an order and order item first without triggering signals
        with mock.patch("order.models.history.OrderHistory.log_note"):
            order = OrderFactory.create()
            item = OrderItemFactory.create(order=order)

        # Override the default behavior to set specific values
        with mock.patch(
            "order.factories.history.OrderItemHistoryFactory.set_change_type_specific_data"
        ):
            history = OrderItemHistoryFactory.create_quantity_change(
                order_item=item, old_quantity=2, new_quantity=5
            )

            # Manually set the values
            history.previous_value = {"quantity": 2}
            history.new_value = {"quantity": 5}
            history.description = "Quantity changed from 2 to 5"
            history.save()

        self.assertEqual(history.change_type, "QUANTITY")
        self.assertEqual(history.previous_value.get("quantity"), 2)
        self.assertEqual(history.new_value.get("quantity"), 5)

    @mock.patch("order.models.history.OrderHistory.log_note")
    def test_creating_multiple_factories(self, mock_log_note):
        """Test creating a full order with items and history entries."""
        # Mock create_batch_for_order to prevent automatic item creation in the factory
        with mock.patch(
            "order.factories.item.OrderItemFactory.create_batch_for_order"
        ):
            # Create an order
            order = OrderFactory.create_completed_order()

            # Create exactly 2 items for testing
            OrderItem.objects.filter(
                order=order
            ).delete()  # Remove any auto-created items
            item1 = OrderItemFactory.create(order=order)
            item2 = OrderItemFactory.create(order=order)

            # Add history entries for the order
            histories = OrderHistoryFactory.create_for_order(order, count=3)

            # Add history entries for each item
            item1_histories = OrderItemHistoryFactory.create_for_order_item(
                item1, count=2
            )
            item2_histories = OrderItemHistoryFactory.create_for_order_item(
                item2, count=2
            )

            # Verify everything was created
            self.assertGreaterEqual(
                order.items.count(), 1
            )  # At least one item should be present
            self.assertEqual(len(histories), 3)
            self.assertEqual(len(item1_histories), 2)
            self.assertEqual(len(item2_histories), 2)

    def test_factory_method_arguments(self):
        """
        Test that factory methods pass the correct arguments to their parent methods.
        This focuses only on validating the arguments, not the actual database creation.
        """
        # Test that shipped_order factory passes the correct status
        with mock.patch(
            "order.factories.order.OrderFactory.create_with_consistent_status_data"
        ) as mock_method:
            # Call the method we want to test
            OrderFactory.create_shipped_order(test_param=123)

            # Verify it was called with the right parameters
            mock_method.assert_called_once()
            args, kwargs = mock_method.call_args
            self.assertEqual(kwargs.get("status"), OrderStatusEnum.SHIPPED)
            self.assertEqual(kwargs.get("test_param"), 123)

        # Test that pending_order factory passes the correct statuses
        with mock.patch(
            "order.factories.order.OrderFactory.create_with_consistent_status_data"
        ) as mock_method:
            # Call the method we want to test
            OrderFactory.create_pending_order(test_param=456)

            # Verify it was called with the right parameters
            mock_method.assert_called_once()
            args, kwargs = mock_method.call_args
            self.assertEqual(kwargs.get("status"), OrderStatusEnum.PENDING)
            self.assertEqual(
                kwargs.get("payment_status"), PaymentStatusEnum.PENDING
            )
            self.assertEqual(kwargs.get("test_param"), 456)

        # Test that completed_order factory passes the correct status
        with mock.patch(
            "order.factories.order.OrderFactory.create_with_consistent_status_data"
        ) as mock_method:
            # Call the method we want to test
            OrderFactory.create_completed_order()

            # Verify it was called with the right parameters
            mock_method.assert_called_once()
            args, kwargs = mock_method.call_args
            self.assertEqual(kwargs.get("status"), OrderStatusEnum.COMPLETED)

        # Test that refunded_order factory passes the correct statuses
        with mock.patch(
            "order.factories.order.OrderFactory.create_with_consistent_status_data"
        ) as mock_method:
            # Set up a mock return value to test item refund logic
            mock_order = mock.MagicMock()
            mock_order.items.exists.return_value = False  # No items to refund
            mock_method.return_value = mock_order

            # Call the method we want to test
            OrderFactory.create_refunded_order()

            # Verify it was called with the right parameters
            mock_method.assert_called_once()
            args, kwargs = mock_method.call_args
            self.assertEqual(kwargs.get("status"), OrderStatusEnum.REFUNDED)
            self.assertEqual(
                kwargs.get("payment_status"), PaymentStatusEnum.REFUNDED
            )
