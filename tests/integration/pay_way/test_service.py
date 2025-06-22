from unittest import mock

from django.test import TestCase

from order.enum.status import PaymentStatus
from order.factories import OrderFactory
from pay_way.factories import PayWayFactory
from pay_way.services import PayWayService


class PayWayServiceTestCase(TestCase):
    def setUp(self):
        self.online_pay_way = PayWayFactory(
            active=True,
            provider_code="stripe",
            is_online_payment=True,
            requires_confirmation=False,
        )

        self.offline_pay_way_with_confirmation = PayWayFactory(
            active=True,
            provider_code="",
            is_online_payment=False,
            requires_confirmation=True,
        )

        self.offline_pay_way_without_confirmation = PayWayFactory(
            active=True,
            provider_code="",
            is_online_payment=False,
            requires_confirmation=False,
        )

        self.order = OrderFactory(
            pay_way=None,
            payment_status=PaymentStatus.PENDING,
            payment_id="",
            payment_method="",
        )

    @mock.patch("pay_way.services.get_payment_provider")
    def test_get_provider_for_pay_way(self, mock_get_payment_provider):
        mock_provider = mock.MagicMock()
        mock_get_payment_provider.return_value = mock_provider

        provider = PayWayService.get_provider_for_pay_way(self.online_pay_way)
        self.assertEqual(provider, mock_provider)
        mock_get_payment_provider.assert_called_once_with(
            self.online_pay_way.provider_code
        )

        mock_get_payment_provider.reset_mock()
        provider = PayWayService.get_provider_for_pay_way(
            self.offline_pay_way_with_confirmation
        )
        self.assertIsNone(provider)
        mock_get_payment_provider.assert_not_called()

    @mock.patch("pay_way.services.PayWayService.get_provider_for_pay_way")
    def test_process_offline_payment_without_confirmation(
        self, mock_get_provider
    ):
        success, data = PayWayService.process_payment(
            pay_way=self.offline_pay_way_without_confirmation, order=self.order
        )

        self.assertTrue(success)
        self.assertEqual(data["payment_id"], f"OFFLINE_{self.order.id}")
        self.assertEqual(data["status"], PaymentStatus.PENDING)

        self.order.refresh_from_db()

        self.assertEqual(self.order.payment_status, PaymentStatus.PENDING)
        self.assertEqual(self.order.payment_id, f"OFFLINE_{self.order.id}")
        self.assertFalse(mock_get_provider.called)

    @mock.patch("pay_way.services.PayWayService.get_provider_for_pay_way")
    def test_process_offline_payment_with_confirmation(self, mock_get_provider):
        success, data = PayWayService.process_payment(
            pay_way=self.offline_pay_way_with_confirmation, order=self.order
        )

        self.assertTrue(success)
        self.assertEqual(data["payment_id"], f"OFFLINE_{self.order.id}")
        self.assertEqual(data["status"], PaymentStatus.PENDING)

        self.order.refresh_from_db()

        self.assertEqual(self.order.payment_status, PaymentStatus.PENDING)
        self.assertEqual(self.order.payment_id, "")
        self.assertFalse(mock_get_provider.called)

    @mock.patch("pay_way.services.PayWayService.get_provider_for_pay_way")
    def test_process_online_payment_success(self, mock_get_provider):
        mock_provider = mock.MagicMock()
        mock_provider.process_payment.return_value = (
            True,
            {
                "payment_id": "TEST_PAYMENT_123",
                "status": PaymentStatus.COMPLETED,
                "amount": str(self.order.total_price.amount),
                "currency": self.order.total_price.currency,
                "provider": "stripe",
            },
        )
        mock_get_provider.return_value = mock_provider

        success, data = PayWayService.process_payment(
            pay_way=self.online_pay_way, order=self.order
        )

        self.assertTrue(success)
        self.assertEqual(data["payment_id"], "TEST_PAYMENT_123")
        self.assertEqual(data["status"], PaymentStatus.COMPLETED)

        self.order.refresh_from_db()

        self.assertEqual(self.order.payment_status, PaymentStatus.COMPLETED)
        self.assertEqual(self.order.payment_id, "TEST_PAYMENT_123")
        self.assertTrue(self.order.is_paid)

        mock_get_provider.assert_called_once_with(self.online_pay_way)
        mock_provider.process_payment.assert_called_once()

    @mock.patch("pay_way.services.PayWayService.get_provider_for_pay_way")
    def test_process_online_payment_failure(self, mock_get_provider):
        mock_provider = mock.MagicMock()
        mock_provider.process_payment.return_value = (
            False,
            {"error": "Payment declined"},
        )
        mock_get_provider.return_value = mock_provider

        success, data = PayWayService.process_payment(
            pay_way=self.online_pay_way, order=self.order
        )

        self.assertFalse(success)
        self.assertEqual(data["error"], "Payment declined")

        self.order.refresh_from_db()

        self.assertEqual(self.order.payment_status, PaymentStatus.PENDING)
        self.assertEqual(self.order.payment_id, "")

    @mock.patch("pay_way.services.PayWayService.get_provider_for_pay_way")
    def test_check_payment_status_online(self, mock_get_provider):
        self.order.payment_id = "TEST_PAYMENT_123"
        self.order.save()

        mock_provider = mock.MagicMock()
        mock_provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {
                "payment_id": "TEST_PAYMENT_123",
                "raw_status": "succeeded",
                "provider": "stripe",
            },
        )
        mock_get_provider.return_value = mock_provider

        status, data = PayWayService.check_payment_status(
            pay_way=self.online_pay_way, order=self.order
        )

        self.assertEqual(status, PaymentStatus.COMPLETED)
        self.assertEqual(data["payment_id"], "TEST_PAYMENT_123")

        self.order.refresh_from_db()

        self.assertEqual(self.order.payment_status, PaymentStatus.COMPLETED)

    def test_check_payment_status_offline(self):
        self.order.payment_id = f"OFFLINE_{self.order.id}"
        self.order.payment_status = PaymentStatus.PENDING
        self.order.save()

        status, data = PayWayService.check_payment_status(
            pay_way=self.offline_pay_way_with_confirmation, order=self.order
        )

        self.assertEqual(status, PaymentStatus.PENDING)
        self.assertEqual(data["provider"], "offline")
        self.assertTrue(data["manual_check_required"])

    @mock.patch("pay_way.services.PayWayService.get_provider_for_pay_way")
    def test_refund_payment_online(self, mock_get_provider):
        self.order.payment_id = "TEST_PAYMENT_123"
        self.order.payment_status = PaymentStatus.COMPLETED
        self.order.mark_as_paid(
            payment_id="TEST_PAYMENT_123", payment_method="Stripe"
        )
        self.order.save()

        mock_provider = mock.MagicMock()
        mock_provider.refund_payment.return_value = (
            True,
            {
                "refund_id": "REFUND_123",
                "status": PaymentStatus.REFUNDED,
                "amount": "full refund",
                "payment_id": "TEST_PAYMENT_123",
            },
        )
        mock_get_provider.return_value = mock_provider

        success, data = PayWayService.refund_payment(
            pay_way=self.online_pay_way, order=self.order
        )

        self.assertTrue(success)
        self.assertEqual(data["refund_id"], "REFUND_123")

        self.order.refresh_from_db()

        self.assertEqual(self.order.payment_status, PaymentStatus.REFUNDED)
        self.assertEqual(self.order.status, "REFUNDED")

    def test_refund_payment_offline(self):
        self.order.payment_id = f"OFFLINE_{self.order.id}"
        self.order.payment_status = PaymentStatus.COMPLETED
        self.order.mark_as_paid(
            payment_id=f"OFFLINE_{self.order.id}",
            payment_method="Bank Transfer",
        )
        self.order.save()

        success, data = PayWayService.refund_payment(
            pay_way=self.offline_pay_way_with_confirmation, order=self.order
        )

        self.assertTrue(success)
        self.assertEqual(data["refund_id"], f"MANUAL_REFUND_{self.order.id}")
        self.assertEqual(data["status"], PaymentStatus.PENDING)
        self.assertEqual(data["provider"], "manual")

        self.order.refresh_from_db()

        self.assertEqual(self.order.payment_status, PaymentStatus.REFUNDED)
        self.assertEqual(self.order.status, "REFUNDED")
