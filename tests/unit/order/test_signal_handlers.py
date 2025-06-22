from unittest import TestCase
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

    @patch("order.signals.order_status_changed.send")
    @patch("order.signals.order_created.send")
    def test_handle_order_post_save_new_order(
        self, mock_order_created, mock_signal
    ):
        handle_order_post_save(
            sender=self.sender, instance=self.order, created=True
        )

        mock_signal.assert_not_called()

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
