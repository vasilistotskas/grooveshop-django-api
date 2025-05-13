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
    def setUp(self):
        self.user = Mock()
        self.user.id = 1
        self.user.email = "customer@example.com"
        self.user.is_authenticated = True

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
        send_order_confirmation(self.order)

        mock_send.assert_called_once_with(self.order)

    @patch("order.notifications.OrderNotificationManager.send_order_shipped")
    def test_send_order_shipped_notification(self, mock_send):
        self.order.tracking_number = "TRACK123456"
        self.order.shipping_carrier = "FedEx"

        send_order_shipped_notification(self.order)

        mock_send.assert_called_once_with(
            self.order, tracking_number="TRACK123456", carrier="FedEx"
        )

    @patch("order.notifications.OrderNotificationManager.send_order_delivered")
    def test_send_order_delivered_notification(self, mock_send):
        send_order_delivered_notification(self.order)

        mock_send.assert_called_once_with(self.order)

    @patch("order.notifications.EmailNotifier")
    @patch("order.notifications.SMSNotifier")
    def test_notification_manager_with_email(self, mock_sms, mock_email):
        mock_email_instance = Mock()
        mock_email.return_value = mock_email_instance

        mock_sms_instance = Mock()
        mock_sms.return_value = mock_sms_instance

        manager = OrderNotificationManager()
        manager.send_order_confirmation(self.order)

        mock_email_instance.send_order_confirmation.assert_called_once_with(
            self.order
        )

    @patch("order.notifications.EmailNotifier")
    @patch("order.notifications.SMSNotifier")
    def test_notification_manager_with_sms(self, mock_sms, mock_email):
        mock_email_instance = Mock()
        mock_email.return_value = mock_email_instance

        mock_sms_instance = Mock()
        mock_sms.return_value = mock_sms_instance

        self.order.phone = "+12025550179"

        manager = OrderNotificationManager()
        manager.send_order_shipped(
            self.order, tracking_number="TRACK123", carrier="UPS"
        )

        mock_sms_instance.send_order_shipped.assert_called_once_with(
            self.order, tracking_number="TRACK123", carrier="UPS"
        )
