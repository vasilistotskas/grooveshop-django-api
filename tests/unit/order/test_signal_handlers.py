"""
Unit tests for order signal handlers.
"""

from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

from order.enum.status_enum import OrderStatusEnum
from order.signals.handlers import handle_order_post_save


class OrderSignalHandlersTestCase(TestCase):
    """Test case for order signal handlers."""

    def setUp(self):
        """Set up test data."""
        # Create a mock order
        self.order = Mock()
        self.order.id = 1
        self.order.status = OrderStatusEnum.PENDING
        self.order._previous_status = None

        # Create a mock sender
        self.sender = MagicMock()

    # Note: Tests for handle_order_created and handle_order_status_changed have been removed
    # because they require database access. These test scenarios are already covered
    # in integration tests in tests/integration/order/test_signals.py.

    @patch("order.signals.order_status_changed.send")
    @patch("order.signals.order_created.send")
    def test_handle_order_post_save_new_order(
        self, mock_order_created, mock_signal
    ):
        """Test post_save handler for new orders."""
        # Call the handler for a newly created order
        handle_order_post_save(
            sender=self.sender, instance=self.order, created=True
        )

        # Verify the status_changed signal was not sent for new orders
        mock_signal.assert_not_called()

        # Verify order_created signal was sent for new orders
        mock_order_created.assert_called_once_with(
            sender=self.sender, order=self.order
        )

    @patch("order.signals.order_status_changed.send")
    def test_handle_order_post_save_status_unchanged(self, mock_signal):
        """Test post_save handler when status is unchanged."""
        # Set the same status for current and previous
        self.order.status = OrderStatusEnum.PENDING
        self.order._previous_status = OrderStatusEnum.PENDING

        # Call the handler
        handle_order_post_save(
            sender=self.sender, instance=self.order, created=False
        )

        # Verify the status_changed signal was not sent
        mock_signal.assert_not_called()

    @patch("order.signals.order_status_changed.send")
    def test_handle_order_post_save_status_changed(self, mock_signal):
        """Test post_save handler when status has changed."""
        # Set different statuses for current and previous
        self.order.status = OrderStatusEnum.PROCESSING
        self.order._previous_status = OrderStatusEnum.PENDING

        # Call the handler
        handle_order_post_save(
            sender=self.sender, instance=self.order, created=False
        )

        # Verify the status_changed signal was sent with correct parameters
        mock_signal.assert_called_once_with(
            sender=self.sender,
            order=self.order,
            old_status=OrderStatusEnum.PENDING,
            new_status=OrderStatusEnum.PROCESSING,
        )

    @patch("order.signals.order_status_changed.send")
    def test_handle_order_post_save_no_previous_status(self, mock_signal):
        """Test post_save handler when there's no previous status attribute."""
        # Remove the _previous_status attribute
        delattr(self.order, "_previous_status")

        # Call the handler
        handle_order_post_save(
            sender=self.sender, instance=self.order, created=False
        )

        # Verify the status_changed signal was not sent
        mock_signal.assert_not_called()
