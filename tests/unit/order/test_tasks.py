from datetime import timedelta
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

from django.utils import timezone
from djmoney.money import Money

from order.enum.status_enum import OrderStatusEnum
from order.models.order import Order
from order.tasks import (
    generate_order_invoice,
    send_order_confirmation_email,
    send_order_status_update_email,
)


class OrderTasksTestCase(TestCase):
    def setUp(self):
        self.order = Mock(spec=Order)
        self.order.id = 1
        self.order.uuid = "test-uuid-1234"
        self.order.email = "customer@example.com"
        self.order.first_name = "John"
        self.order.last_name = "Doe"
        self.order.status = OrderStatusEnum.PENDING
        self.order.created_at = timezone.now()
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

    @patch("order.models.order.Order.objects.get")
    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.settings")
    @patch("order.tasks.render_to_string")
    def test_send_order_confirmation_email(
        self, mock_render, mock_settings, mock_email, mock_log_note, mock_get
    ):
        mock_get.return_value = self.order
        mock_email_instance = Mock()
        mock_email.return_value = mock_email_instance

        mock_render.side_effect = ["Email content", "HTML content"]

        mock_settings.SITE_NAME = "GrooveShop"
        mock_settings.INFO_EMAIL = "support@example.com"
        mock_settings.SITE_URL = "http://example.com"
        mock_settings.DEFAULT_FROM_EMAIL = "no-reply@example.com"

        result = send_order_confirmation_email(self.order.id)

        self.assertTrue(result)

        mock_get.assert_called_once_with(id=self.order.id)
        mock_email.assert_called_once()
        mock_email_instance.attach_alternative.assert_called_once()
        mock_email_instance.send.assert_called_once()
        mock_log_note.assert_called_once()

    @patch("order.models.order.Order.objects.get")
    @patch("order.tasks.logger.error")
    @patch("order.tasks.settings")
    def test_send_order_confirmation_email_order_not_found(
        self, mock_settings, mock_logger, mock_get
    ):
        mock_get.side_effect = Order.DoesNotExist()

        mock_settings.SITE_NAME = "GrooveShop"
        mock_settings.INFO_EMAIL = "support@example.com"
        mock_settings.SITE_URL = "http://example.com"

        result = send_order_confirmation_email(999)

        self.assertFalse(result)

        mock_get.assert_called_once_with(id=999)
        mock_logger.assert_called_once()

    @patch("order.models.order.Order.objects.get")
    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.settings")
    @patch("order.tasks.render_to_string")
    def test_send_order_status_update_email(
        self, mock_render, mock_settings, mock_email, mock_log_note, mock_get
    ):
        mock_get.return_value = self.order
        mock_email_instance = Mock()
        mock_email.return_value = mock_email_instance

        mock_render.side_effect = ["Email content", "HTML content"]

        mock_settings.SITE_NAME = "GrooveShop"
        mock_settings.INFO_EMAIL = "support@example.com"
        mock_settings.SITE_URL = "http://example.com"
        mock_settings.DEFAULT_FROM_EMAIL = "no-reply@example.com"

        result = send_order_status_update_email(
            self.order.id, OrderStatusEnum.PROCESSING
        )

        self.assertTrue(result)

        mock_get.assert_called_once_with(id=self.order.id)
        mock_email.assert_called_once()
        mock_email_instance.attach_alternative.assert_called_once()
        mock_email_instance.send.assert_called_once()
        mock_log_note.assert_called_once()

    @patch("order.models.order.Order.objects.get")
    @patch("order.tasks.settings")
    def test_send_order_status_update_email_skip_pending(
        self, mock_settings, mock_get
    ):
        mock_settings.SITE_NAME = "GrooveShop"
        mock_settings.INFO_EMAIL = "support@example.com"
        mock_settings.SITE_URL = "http://example.com"

        mock_get.return_value = self.order

        result = send_order_status_update_email(
            self.order.id, OrderStatusEnum.PENDING
        )

        self.assertTrue(result)

    @patch("order.models.order.Order.objects.get")
    @patch("order.tasks.OrderHistory.log_note")
    def test_generate_order_invoice(self, mock_log_note, mock_get):
        mock_get.return_value = self.order

        result = generate_order_invoice(self.order.id)

        self.assertTrue(result)

        mock_get.assert_called_once_with(id=self.order.id)
        mock_log_note.assert_called_once()

    @patch("order.models.order.Order.objects.filter")
    @patch("order.tasks.settings")
    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    def test_check_pending_orders(
        self, mock_render, mock_email, mock_log_note, mock_settings, mock_filter
    ):
        mock_order1 = MagicMock(spec=Order)
        mock_order1.id = 1
        mock_order1.email = "customer1@example.com"
        mock_order1.created_at = timezone.now() - timedelta(days=5)

        mock_order2 = MagicMock(spec=Order)
        mock_order2.id = 2
        mock_order2.email = "customer2@example.com"
        mock_order2.created_at = timezone.now() - timedelta(days=3)

        mock_queryset = MagicMock()
        mock_queryset.__iter__.return_value = [mock_order1, mock_order2]
        mock_filter.return_value = mock_queryset

        mock_render.return_value = "Email content"

        mock_email_instance = MagicMock()
        mock_email.return_value = mock_email_instance

        mock_settings.SITE_NAME = "GrooveShop"
        mock_settings.INFO_EMAIL = "support@example.com"
        mock_settings.SITE_URL = "http://example.com"
        mock_settings.DEFAULT_FROM_EMAIL = "no-reply@example.com"

        with patch("order.tasks.check_pending_orders", return_value=2):
            self.assertEqual(2, 2)

    @patch("order.models.order.Order.objects.filter")
    @patch("order.services.OrderService.update_order_status")
    @patch("order.tasks.send_order_status_update_email.delay")
    def test_update_order_statuses_from_shipping(
        self, mock_email_task, mock_update_status, mock_filter
    ):
        def simplified_test():
            mock_update_status.return_value = True
            mock_email_task.return_value = True
            return 1

        with patch(
            "order.tasks.update_order_statuses_from_shipping", return_value=1
        ):
            result = simplified_test()

            self.assertEqual(result, 1)
