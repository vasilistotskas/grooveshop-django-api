"""
Unit tests for order notifications.
"""

from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

from django.utils import timezone
from djmoney.money import Money

from order.enum.status_enum import OrderStatusEnum
from order.models.order import Order
from order.notifications import (
    OrderNotificationManager,
    send_order_confirmation,
    send_order_delivered_notification,
    send_order_shipped_notification,
)


class OrderNotificationTestCase(TestCase):
    """Test case for order notifications."""

    def setUp(self):
        """Set up test data."""
        # Create a mock user
        self.user = Mock()
        self.user.id = 1
        self.user.email = "customer@example.com"
        self.user.is_authenticated = True

        # Create a mock order
        self.order = Mock(spec=Order)
        self.order.id = 1
        self.order.uuid = "test-uuid-1234"
        self.order.user = self.user
        self.order.email = "customer@example.com"
        self.order.first_name = "John"
        self.order.last_name = "Doe"
        self.order.status = OrderStatusEnum.PENDING
        self.order.created_at = timezone.now()
        self.order.updated_at = timezone.now()
        self.order.total = Money("135.00", "USD")

        # Set up mock items
        item1 = Mock()
        item1.product = Mock()
        item1.product.name = "Test Product 1"
        item1.quantity = 2
        item1.price = Money("50.00", "USD")
        item1.total = Money("100.00", "USD")

        item2 = Mock()
        item2.product = Mock()
        item2.product.name = "Test Product 2"
        item2.quantity = 1
        item2.price = Money("30.00", "USD")
        item2.total = Money("30.00", "USD")

        self.order.items = MagicMock()
        self.order.items.all.return_value = [item1, item2]

    @patch(
        "order.notifications.OrderNotificationManager.send_order_confirmation"
    )
    def test_send_order_confirmation(self, mock_send):
        """Test sending order confirmation notification."""
        # Call the function
        send_order_confirmation(self.order)

        # Verify notification was sent
        mock_send.assert_called_once_with(self.order)

    @patch("order.notifications.OrderNotificationManager.send_order_shipped")
    def test_send_order_shipped_notification(self, mock_send):
        """Test sending order shipped notification."""
        # Set up shipping information
        self.order.tracking_number = "TRACK123456"
        self.order.shipping_carrier = "FedEx"

        # Call the function
        send_order_shipped_notification(self.order)

        # Verify notification was sent
        mock_send.assert_called_once_with(
            self.order, tracking_number="TRACK123456", carrier="FedEx"
        )

    @patch("order.notifications.OrderNotificationManager.send_order_delivered")
    def test_send_order_delivered_notification(self, mock_send):
        """Test sending order delivered notification."""
        # Call the function
        send_order_delivered_notification(self.order)

        # Verify notification was sent
        mock_send.assert_called_once_with(self.order)

    @patch("order.notifications.EmailNotifier")
    @patch("order.notifications.SMSNotifier")
    def test_notification_manager_with_email(self, mock_sms, mock_email):
        """Test notification manager sending emails."""
        # Set up mock notifiers
        mock_email_instance = Mock()
        mock_email.return_value = mock_email_instance

        mock_sms_instance = Mock()
        mock_sms.return_value = mock_sms_instance

        # Create a manager instance and send notification
        manager = OrderNotificationManager()
        manager.send_order_confirmation(self.order)

        # Verify email notifier was used
        mock_email_instance.send_order_confirmation.assert_called_once_with(
            self.order
        )

    @patch("order.notifications.EmailNotifier")
    @patch("order.notifications.SMSNotifier")
    def test_notification_manager_with_sms(self, mock_sms, mock_email):
        """Test notification manager sending SMS."""
        # Set up mock notifiers
        mock_email_instance = Mock()
        mock_email.return_value = mock_email_instance

        mock_sms_instance = Mock()
        mock_sms.return_value = mock_sms_instance

        # Set up phone number
        self.order.phone = "+12025550179"

        # Create a manager instance and send notification
        manager = OrderNotificationManager()
        manager.send_order_shipped(
            self.order, tracking_number="TRACK123", carrier="UPS"
        )

        # Verify SMS notifier was used
        mock_sms_instance.send_order_shipped.assert_called_once_with(
            self.order, tracking_number="TRACK123", carrier="UPS"
        )

    # Signal handler tests removed - these are covered in integration tests
