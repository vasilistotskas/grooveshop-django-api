from unittest.mock import Mock, patch

import pytest
from django.test import TestCase as DjangoTestCase

from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from order.notifications import (
    OrderNotificationManager,
    send_order_confirmation,
    send_order_delivered_notification,
    send_order_shipped_notification,
)
from user.factories.account import UserAccountFactory


@pytest.mark.django_db
class OrderNotificationTestCase(DjangoTestCase):
    def setUp(self):
        self.user = UserAccountFactory.create(email="customer@example.com")

        self.order = OrderFactory.create(
            user=self.user,
            email="customer@example.com",
            first_name="John",
            last_name="Doe",
            status=OrderStatus.PENDING,
        )

    @patch("order.notifications.EmailNotifier.send_order_confirmation")
    def test_send_order_confirmation(self, mock_email_send):
        send_order_confirmation(self.order)

        mock_email_send.assert_called_once()

    @patch("order.notifications.EmailNotifier.send_order_shipped")
    @patch("order.notifications.SMSNotifier.send_order_shipped")
    def test_send_order_shipped_notification(
        self, mock_sms_send, mock_email_send
    ):
        self.order.tracking_number = "TRACK123456"
        self.order.shipping_carrier = "FedEx"
        self.order.save()

        send_order_shipped_notification(self.order)

        mock_email_send.assert_called_once()

    @patch("order.notifications.EmailNotifier.send_order_delivered")
    def test_send_order_delivered_notification(self, mock_email_send):
        send_order_delivered_notification(self.order)

        mock_email_send.assert_called_once()

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

        self.user.phone = "+12025550179"
        self.user.save()

        manager = OrderNotificationManager()
        manager.send_order_shipped(
            self.order, tracking_number="TRACK123", carrier="UPS"
        )

        mock_sms_instance.send_order_shipped.assert_called_once_with(
            self.order, tracking_number="TRACK123", carrier="UPS"
        )
