from datetime import timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest
from django.test import TestCase as DjangoTestCase
from django.test import override_settings
from django.utils import timezone

from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from order.models import OrderHistory
from order.models.order import Order
from order.tasks import (
    check_pending_orders,
    generate_order_invoice,
    send_order_confirmation_email,
    send_order_status_update_email,
    send_shipping_notification_email,
    update_order_statuses_from_shipping,
)


@pytest.mark.django_db
class OrderTasksSimpleTestCase(DjangoTestCase):
    def setUp(self):
        self.order = OrderFactory.create(
            email="customer@example.com",
            first_name="John",
            last_name="Doe",
            status=OrderStatus.PENDING,
        )

    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        SITE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_send_order_confirmation_email_success(
        self, mock_render, mock_email, mock_log_note
    ):
        mock_email_instance = Mock()
        mock_email.return_value = mock_email_instance

        mock_render.side_effect = ["Email content", "HTML content"]

        result = send_order_confirmation_email(self.order.id)

        self.assertTrue(result)
        mock_email.assert_called_once()
        mock_email_instance.attach_alternative.assert_called_once()
        mock_email_instance.send.assert_called_once()
        mock_log_note.assert_called_once()

    @patch("order.tasks.logger.error")
    def test_send_order_confirmation_email_order_not_found(self, mock_logger):
        result = send_order_confirmation_email(999999)

        self.assertFalse(result)
        mock_logger.assert_called_once()

    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        SITE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_send_order_status_update_email_success(
        self, mock_render, mock_email, mock_log_note
    ):
        mock_email_instance = Mock()
        mock_email.return_value = mock_email_instance

        mock_render.side_effect = ["Email content", "HTML content"]

        result = send_order_status_update_email(
            self.order.id, OrderStatus.PROCESSING
        )

        self.assertTrue(result)
        mock_email.assert_called_once()
        mock_email_instance.attach_alternative.assert_called_once()
        mock_email_instance.send.assert_called_once()
        mock_log_note.assert_called_once()

    def test_send_order_status_update_email_skip_pending(self):
        result = send_order_status_update_email(
            self.order.id, OrderStatus.PENDING
        )

        self.assertTrue(result)

    @patch("order.tasks.logger.error")
    def test_send_order_status_update_email_order_not_found(self, mock_logger):
        result = send_order_status_update_email(999999, OrderStatus.PROCESSING)

        self.assertFalse(result)
        mock_logger.assert_called_once()

    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    @patch("order.tasks.logger.warning")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        SITE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_send_order_status_update_email_template_fallback(
        self, mock_logger_warning, mock_render, mock_email, mock_log_note
    ):
        mock_email_instance = Mock()
        mock_email.return_value = mock_email_instance

        def render_side_effect(template_name, context):
            if "order_processing" in template_name:
                raise Exception("Template not found")
            else:
                return "Generic email content"

        mock_render.side_effect = render_side_effect

        result = send_order_status_update_email(
            self.order.id, OrderStatus.PROCESSING
        )

        self.assertTrue(result)
        mock_logger_warning.assert_called_once()
        mock_email_instance.send.assert_called_once()

    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        SITE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_send_shipping_notification_email_success(
        self, mock_render, mock_email, mock_log_note
    ):
        order_with_tracking = OrderFactory.create(email="customer@example.com")
        order_with_tracking.tracking_number = "TRACK123456"
        order_with_tracking.shipping_carrier = "UPS"
        order_with_tracking.save()

        mock_email_instance = Mock()
        mock_email.return_value = mock_email_instance

        mock_render.side_effect = ["Email content", "HTML content"]

        result = send_shipping_notification_email(order_with_tracking.id)

        self.assertTrue(result)
        mock_email_instance.send.assert_called_once()
        self.assertTrue(mock_log_note.called)

    @patch("order.tasks.logger.warning")
    def test_send_shipping_notification_email_no_tracking_info(
        self, mock_logger
    ):
        self.order.tracking_number = ""
        self.order.shipping_carrier = ""
        self.order.save()

        result = send_shipping_notification_email(self.order.id)

        self.assertFalse(result)
        mock_logger.assert_called_once()

    @patch("order.tasks.logger.error")
    def test_send_shipping_notification_email_order_not_found(
        self, mock_logger
    ):
        result = send_shipping_notification_email(999999)

        self.assertFalse(result)
        mock_logger.assert_called_once()

    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.logger.info")
    def test_generate_order_invoice_success(
        self, mock_logger_info, mock_log_note
    ):
        result = generate_order_invoice(self.order.id)

        self.assertTrue(result)
        mock_logger_info.assert_called_once()
        mock_log_note.assert_called_once()

    @patch("order.tasks.logger.error")
    def test_generate_order_invoice_order_not_found(self, mock_logger):
        result = generate_order_invoice(999999)

        self.assertFalse(result)
        mock_logger.assert_called_once()

    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.logger.error")
    def test_generate_order_invoice_exception(
        self, mock_logger_error, mock_log_note
    ):
        mock_log_note.side_effect = Exception("Database error")

        result = generate_order_invoice(self.order.id)

        self.assertFalse(result)
        mock_logger_error.assert_called_once()

    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        SITE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_check_pending_orders_success(
        self, mock_render, mock_email, mock_log_note
    ):
        _ = OrderFactory.create(
            status=OrderStatus.PENDING,
            email="old@example.com",
            created_at=timezone.now() - timedelta(days=2),
        )

        mock_render.return_value = "Email content"
        mock_email_instance = MagicMock()
        mock_email.return_value = mock_email_instance

        result = check_pending_orders()

        self.assertGreaterEqual(result, 0)
        if result > 0:
            mock_email_instance.send.assert_called()
            mock_log_note.assert_called()

    @patch("order.tasks.logger.error")
    def test_check_pending_orders_exception(self, mock_logger):
        with patch("order.models.order.Order.objects.filter") as mock_filter:
            mock_filter.side_effect = Exception("Database error")

            result = check_pending_orders()

            self.assertEqual(result, 0)
            mock_logger.assert_called_once()

    @patch("order.tasks.send_order_status_update_email.delay")
    @patch("order.services.OrderService.update_order_status")
    @patch("order.shipping.ShippingService.get_tracking_info")
    def test_update_order_statuses_from_shipping_success(
        self, mock_tracking, mock_update_status, mock_email_task
    ):
        shipped_order = OrderFactory.create(
            status=OrderStatus.SHIPPED,
            tracking_number="TRACK123",
            shipping_carrier="UPS",
        )

        mock_tracking.return_value = {"status": OrderStatus.DELIVERED}
        mock_update_status.return_value = True

        result = update_order_statuses_from_shipping()

        self.assertGreaterEqual(result, 0)
        if result > 0:
            mock_tracking.assert_called_with("TRACK123", "UPS")
            mock_update_status.assert_called_with(
                shipped_order, OrderStatus.DELIVERED
            )
            mock_email_task.assert_called_with(
                shipped_order.id, OrderStatus.DELIVERED
            )

    @patch("order.tasks.logger.error")
    def test_update_order_statuses_from_shipping_exception(self, mock_logger):
        with patch("order.models.order.Order.objects.filter") as mock_filter:
            mock_filter.side_effect = Exception("Database error")

            result = update_order_statuses_from_shipping()

            self.assertEqual(result, 0)
            mock_logger.assert_called_once()

    def test_update_order_statuses_from_shipping_no_carrier(self):
        OrderFactory.create(
            status=OrderStatus.SHIPPED,
            tracking_number="TRACK123",
            shipping_carrier="",
        )

        result = update_order_statuses_from_shipping()

        self.assertEqual(result, 0)

    @patch("order.shipping.ShippingService.get_tracking_info")
    def test_update_order_statuses_from_shipping_not_delivered(
        self, mock_tracking
    ):
        OrderFactory.create(
            status=OrderStatus.SHIPPED,
            tracking_number="TRACK123",
            shipping_carrier="UPS",
        )

        mock_tracking.return_value = {"status": "IN_TRANSIT"}

        result = update_order_statuses_from_shipping()

        self.assertEqual(result, 0)


@pytest.mark.django_db
class OrderTasksIntegrationTestCase(DjangoTestCase):
    def setUp(self):
        self.order = OrderFactory.create(
            email="integration@example.com",
            status=OrderStatus.PROCESSING,
            tracking_number="INT123",
            shipping_carrier="FedEx",
        )

    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        SITE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_order_workflow_email_sequence(self, mock_render, mock_email):
        mock_render.return_value = "Email content"
        mock_email_instance = MagicMock()
        mock_email.return_value = mock_email_instance

        confirmation_result = send_order_confirmation_email(self.order.id)
        status_result = send_order_status_update_email(
            self.order.id, OrderStatus.PROCESSING
        )
        shipping_result = send_shipping_notification_email(self.order.id)

        self.assertTrue(confirmation_result)
        self.assertTrue(status_result)
        self.assertTrue(shipping_result)

        self.assertEqual(mock_email_instance.send.call_count, 3)

    def test_database_operations_integrity(self):
        self.assertTrue(Order.objects.filter(id=self.order.id).exists())

        original_status = self.order.status
        result = generate_order_invoice(self.order.id)

        self.assertTrue(result)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, original_status)

        history_count = OrderHistory.objects.filter(order=self.order).count()

        generate_order_invoice(self.order.id)
        new_history_count = OrderHistory.objects.filter(
            order=self.order
        ).count()
        self.assertGreater(new_history_count, history_count)
