from django.test import TestCase
from unittest.mock import MagicMock, Mock, patch

from order.enum.status import OrderStatus
from order.signals.handlers import handle_order_post_save


class OrderSignalHandlersTestCase(TestCase):
    def setUp(self):
        self.order = Mock()
        self.order.id = 1
        self.order.status = OrderStatus.PENDING
        self.order._previous_status = None

        self.sender = MagicMock()

    @patch("order.signals.order_created.send")
    @patch("django.db.transaction.on_commit")
    def test_handle_order_post_save_new_order(
        self, mock_on_commit, mock_order_created
    ):
        """Test that order_created signal is deferred via transaction.on_commit"""
        handle_order_post_save(
            sender=self.sender, instance=self.order, created=True
        )

        # Signal should NOT be called immediately
        mock_order_created.assert_not_called()

        # But on_commit should be called to schedule the signal
        mock_on_commit.assert_called_once()

        # Execute the deferred callback to verify it works
        callback = mock_on_commit.call_args[0][0]
        callback()

        # Now the signal should be called
        mock_order_created.assert_called_once_with(
            sender=self.sender, order=self.order
        )

    @patch("order.signals.order_status_changed.send")
    def test_handle_order_post_save_status_unchanged(self, mock_signal):
        self.order.status = OrderStatus.PENDING
        self.order._previous_status = OrderStatus.PENDING

        handle_order_post_save(
            sender=self.sender, instance=self.order, created=False
        )

        mock_signal.assert_not_called()

    @patch("order.signals.order_status_changed.send")
    def test_handle_order_post_save_status_changed(self, mock_signal):
        self.order.status = OrderStatus.PROCESSING
        self.order._previous_status = OrderStatus.PENDING

        handle_order_post_save(
            sender=self.sender, instance=self.order, created=False
        )

        mock_signal.assert_called_once_with(
            sender=self.sender,
            order=self.order,
            old_status=OrderStatus.PENDING,
            new_status=OrderStatus.PROCESSING,
        )

    @patch("order.signals.order_status_changed.send")
    def test_handle_order_post_save_no_previous_status(self, mock_signal):
        delattr(self.order, "_previous_status")

        handle_order_post_save(
            sender=self.sender, instance=self.order, created=False
        )

        mock_signal.assert_not_called()
