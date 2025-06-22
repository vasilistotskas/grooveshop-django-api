from decimal import Decimal
from unittest import TestCase, mock

from djmoney.money import Money

from order.enum.status import PaymentStatus
from order.payment import (
    PayPalPaymentProvider,
    StripePaymentProvider,
    get_payment_provider,
)


class PaymentModuleTestCase(TestCase):
    def test_payment_status_enum(self):
        self.assertEqual(PaymentStatus.PENDING.value, "PENDING")
        self.assertEqual(PaymentStatus.PROCESSING.value, "PROCESSING")
        self.assertEqual(PaymentStatus.COMPLETED.value, "COMPLETED")
        self.assertEqual(PaymentStatus.FAILED.value, "FAILED")
        self.assertEqual(PaymentStatus.REFUNDED.value, "REFUNDED")
        self.assertEqual(
            PaymentStatus.PARTIALLY_REFUNDED.value, "PARTIALLY_REFUNDED"
        )
        self.assertEqual(PaymentStatus.CANCELED.value, "CANCELED")

    @mock.patch("order.payment.settings")
    def test_stripe_payment_provider_init(self, mock_settings):
        mock_settings.STRIPE_API_KEY = "test_api_key"
        mock_settings.STRIPE_WEBHOOK_SECRET = "test_webhook_secret"

        provider = StripePaymentProvider()

        self.assertEqual(provider.api_key, "test_api_key")
        self.assertEqual(provider.webhook_secret, "test_webhook_secret")

    @mock.patch("order.payment.settings")
    def test_paypal_payment_provider_init(self, mock_settings):
        mock_settings.PAYPAL_CLIENT_ID = "test_client_id"
        mock_settings.PAYPAL_CLIENT_SECRET = "test_client_secret"

        provider = PayPalPaymentProvider()

        self.assertEqual(provider.client_id, "test_client_id")
        self.assertEqual(provider.client_secret, "test_client_secret")

    @mock.patch("order.payment.logger")
    def test_stripe_process_payment(self, mock_logger):
        provider = StripePaymentProvider()
        amount = Money(amount=Decimal("100.00"), currency="USD")
        order_id = "test_order_id"

        success, payment_data = provider.process_payment(amount, order_id)

        self.assertTrue(success)
        self.assertEqual(payment_data["payment_id"], f"pi_{order_id}_mock")
        self.assertEqual(payment_data["status"], PaymentStatus.COMPLETED)
        self.assertEqual(payment_data["amount"], str(amount.amount))
        self.assertEqual(payment_data["currency"], amount.currency)
        self.assertEqual(payment_data["provider"], "stripe")

        mock_logger.info.assert_called_once()

    @mock.patch("order.payment.logger")
    def test_paypal_process_payment(self, mock_logger):
        provider = PayPalPaymentProvider()
        amount = Money(amount=Decimal("100.00"), currency="USD")
        order_id = "test_order_id"

        success, payment_data = provider.process_payment(amount, order_id)

        self.assertTrue(success)
        self.assertEqual(payment_data["payment_id"], f"PP_{order_id}_mock")
        self.assertEqual(payment_data["status"], PaymentStatus.COMPLETED)
        self.assertEqual(payment_data["amount"], str(amount.amount))
        self.assertEqual(payment_data["currency"], amount.currency)
        self.assertEqual(payment_data["provider"], "paypal")

        mock_logger.info.assert_called_once()

    @mock.patch("order.payment.logger")
    def test_stripe_refund_payment(self, mock_logger):
        provider = StripePaymentProvider()
        payment_id = "test_payment_id"
        amount = Money(amount=Decimal("50.00"), currency="USD")

        success, refund_data = provider.refund_payment(payment_id, amount)

        self.assertTrue(success)
        self.assertEqual(refund_data["refund_id"], f"re_{payment_id}_mock")
        self.assertEqual(refund_data["status"], PaymentStatus.REFUNDED)
        self.assertEqual(refund_data["amount"], str(amount.amount))
        self.assertEqual(refund_data["payment_id"], payment_id)

        mock_logger.info.assert_called_once()

    @mock.patch("order.payment.logger")
    def test_stripe_get_payment_status(self, mock_logger):
        provider = StripePaymentProvider()
        payment_id = "test_payment_id"

        status, status_data = provider.get_payment_status(payment_id)

        self.assertEqual(status, PaymentStatus.COMPLETED)
        self.assertEqual(status_data["payment_id"], payment_id)
        self.assertEqual(status_data["raw_status"], "succeeded")
        self.assertEqual(status_data["provider"], "stripe")

        mock_logger.info.assert_called_once()

    def test_get_payment_provider(self):
        provider = get_payment_provider("stripe")
        self.assertIsInstance(provider, StripePaymentProvider)

        provider = get_payment_provider("paypal")
        self.assertIsInstance(provider, PayPalPaymentProvider)

        with self.assertRaises(ValueError):
            get_payment_provider("invalid_provider")
