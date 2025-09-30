from decimal import Decimal
from unittest import TestCase, mock

from django.conf import settings
from djmoney.money import Money
from djstripe.models import PaymentIntent

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
        mock_settings.STRIPE_TEST_SECRET_KEY = "test_api_key"
        mock_settings.DJSTRIPE_WEBHOOK_SECRET = "test_webhook_secret"
        mock_settings.STRIPE_LIVE_MODE = False

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

    @mock.patch("order.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payment.logger")
    def test_stripe_process_payment(self, mock_logger, mock_stripe_create):
        mock_payment_intent = mock.Mock()
        mock_payment_intent.id = "pi_test_order_id_mock"
        mock_payment_intent.status = "succeeded"
        mock_payment_intent.client_secret = "pi_test_secret"
        mock_payment_intent.next_action = None
        mock_stripe_create.return_value = mock_payment_intent

        with mock.patch("order.payment.PaymentIntent.sync_from_stripe_data"):
            provider = StripePaymentProvider()
            amount = Money(
                amount=Decimal("100.00"), currency=settings.DEFAULT_CURRENCY
            )
            order_id = "test_order_id"

            success, payment_data = provider.process_payment(amount, order_id)

            self.assertTrue(success)
            self.assertEqual(
                payment_data["payment_id"], "pi_test_order_id_mock"
            )
            self.assertEqual(payment_data["status"], PaymentStatus.COMPLETED)

    @mock.patch("order.payment.logger")
    def test_paypal_process_payment(self, mock_logger):
        provider = PayPalPaymentProvider()
        amount = Money(
            amount=Decimal("100.00"), currency=settings.DEFAULT_CURRENCY
        )
        order_id = "test_order_id"

        success, payment_data = provider.process_payment(amount, order_id)

        self.assertTrue(success)
        self.assertEqual(payment_data["payment_id"], f"PP_{order_id}_mock")
        self.assertEqual(payment_data["status"], PaymentStatus.COMPLETED)
        self.assertEqual(payment_data["amount"], str(amount.amount))
        self.assertEqual(payment_data["currency"], str(amount.currency))
        self.assertEqual(payment_data["provider"], "paypal")

        mock_logger.info.assert_called_once()

    @mock.patch("order.payment.stripe.Refund.create")
    @mock.patch("order.payment.logger")
    def test_stripe_refund_payment(self, mock_logger, mock_stripe_refund):
        mock_refund = mock.Mock()
        mock_refund.id = "re_test_payment_id_mock"
        mock_refund.status = "succeeded"
        mock_stripe_refund.return_value = mock_refund

        with mock.patch("order.payment.Refund.sync_from_stripe_data"):
            provider = StripePaymentProvider()
            payment_id = "test_payment_id"
            amount = Money(
                amount=Decimal("50.00"), currency=settings.DEFAULT_CURRENCY
            )

            success, refund_data = provider.refund_payment(payment_id, amount)

            self.assertTrue(success)
            self.assertEqual(
                refund_data["refund_id"], "re_test_payment_id_mock"
            )

    @mock.patch("order.payment.stripe.PaymentIntent.retrieve")
    @mock.patch("order.payment.PaymentIntent.sync_from_stripe_data")
    @mock.patch("order.payment.logger")
    def test_stripe_get_payment_status(
        self, mock_logger, mock_sync, mock_retrieve
    ):
        mock_payment_intent = mock.Mock()
        mock_payment_intent.id = "test_payment_id"
        mock_payment_intent.status = "succeeded"
        mock_payment_intent.amount = 10000
        mock_payment_intent.currency = "eur"
        mock_payment_intent.created = 1234567890

        mock_retrieve.return_value = mock_payment_intent
        mock_djstripe_pi = mock.Mock()
        mock_djstripe_pi.created = "2024-01-01"
        mock_sync.return_value = mock_djstripe_pi

        with mock.patch(
            "order.payment.PaymentIntent.objects.get",
            side_effect=PaymentIntent.DoesNotExist,
        ):
            provider = StripePaymentProvider()
            payment_id = "test_payment_id"

            status, status_data = provider.get_payment_status(payment_id)

            self.assertEqual(status, PaymentStatus.COMPLETED)
            self.assertEqual(status_data["payment_id"], payment_id)

    def test_get_payment_provider(self):
        provider = get_payment_provider("stripe")
        self.assertIsInstance(provider, StripePaymentProvider)

        provider = get_payment_provider("paypal")
        self.assertIsInstance(provider, PayPalPaymentProvider)

        with self.assertRaises(ValueError):
            get_payment_provider("invalid_provider")
