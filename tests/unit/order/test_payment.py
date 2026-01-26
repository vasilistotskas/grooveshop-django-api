from decimal import Decimal
from unittest import TestCase, mock

from stripe._error import SignatureVerificationError, StripeError
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


class StripeWebhookSignatureVerificationTestCase(TestCase):
    """
    Test webhook signature verification for StripePaymentProvider.
    """

    @mock.patch("order.payment.stripe.Webhook.construct_event")
    @mock.patch("order.payment.settings")
    def test_verify_webhook_signature_success(
        self, mock_settings, mock_construct_event
    ):
        """
        Test successful webhook signature verification.

        Validates:
        - Uses stripe.Webhook.construct_event
        - Uses DJSTRIPE_WEBHOOK_SECRET from settings
        - Returns verified event dictionary
        """
        # Setup
        mock_settings.STRIPE_TEST_SECRET_KEY = "test_api_key"
        mock_settings.DJSTRIPE_WEBHOOK_SECRET = "test_webhook_secret"
        mock_settings.STRIPE_LIVE_MODE = False

        expected_event = {
            "id": "evt_test_123",
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_test_123"}},
        }
        mock_construct_event.return_value = expected_event

        provider = StripePaymentProvider()
        payload = b'{"test": "payload"}'
        signature = "test_signature"

        # Execute
        result = provider.verify_webhook_signature(payload, signature)

        # Verify
        self.assertEqual(result, expected_event)
        mock_construct_event.assert_called_once_with(
            payload, signature, "test_webhook_secret"
        )

    @mock.patch("order.payment.stripe.Webhook.construct_event")
    @mock.patch("order.payment.settings")
    def test_verify_webhook_signature_invalid_signature(
        self, mock_settings, mock_construct_event
    ):
        """
        Test webhook signature verification with invalid signature.

        Validates:
        - Raises SignatureVerificationError on invalid signature
        - Logs error message
        """
        # Setup
        mock_settings.STRIPE_TEST_SECRET_KEY = "test_api_key"
        mock_settings.DJSTRIPE_WEBHOOK_SECRET = "test_webhook_secret"
        mock_settings.STRIPE_LIVE_MODE = False

        mock_construct_event.side_effect = SignatureVerificationError(
            "Invalid signature", "sig_header"
        )

        provider = StripePaymentProvider()
        payload = b'{"test": "payload"}'
        signature = "invalid_signature"

        # Execute & Verify
        with self.assertRaises(SignatureVerificationError):
            provider.verify_webhook_signature(payload, signature)

    @mock.patch("order.payment.stripe.Webhook.construct_event")
    @mock.patch("order.payment.settings")
    def test_verify_webhook_signature_invalid_payload(
        self, mock_settings, mock_construct_event
    ):
        """
        Test webhook signature verification with invalid payload.

        Validates:
        - Raises ValueError on invalid JSON payload
        - Logs error message
        """
        # Setup
        mock_settings.STRIPE_TEST_SECRET_KEY = "test_api_key"
        mock_settings.DJSTRIPE_WEBHOOK_SECRET = "test_webhook_secret"
        mock_settings.STRIPE_LIVE_MODE = False

        mock_construct_event.side_effect = ValueError("Invalid JSON")

        provider = StripePaymentProvider()
        payload = b"invalid json"
        signature = "test_signature"

        # Execute & Verify
        with self.assertRaises(ValueError):
            provider.verify_webhook_signature(payload, signature)

    @mock.patch("order.payment.settings")
    def test_verify_webhook_signature_missing_secret(self, mock_settings):
        """
        Test webhook signature verification without configured secret.

        Validates:
        - Raises ValueError when DJSTRIPE_WEBHOOK_SECRET is not configured
        - Error message indicates webhook secret not configured
        """
        # Setup
        mock_settings.STRIPE_TEST_SECRET_KEY = "test_api_key"
        mock_settings.DJSTRIPE_WEBHOOK_SECRET = ""
        mock_settings.STRIPE_LIVE_MODE = False

        provider = StripePaymentProvider()
        payload = b'{"test": "payload"}'
        signature = "test_signature"

        # Execute & Verify
        with self.assertRaises(ValueError) as context:
            provider.verify_webhook_signature(payload, signature)

        self.assertIn("Webhook secret not configured", str(context.exception))

    @mock.patch("order.payment.stripe.Webhook.construct_event")
    @mock.patch("order.payment.settings")
    def test_verify_webhook_signature_stripe_error(
        self, mock_settings, mock_construct_event
    ):
        """
        Test webhook signature verification with generic Stripe error.

        Validates:
        - Raises StripeError for other Stripe-related errors
        - Logs error with context
        """
        # Setup
        mock_settings.STRIPE_TEST_SECRET_KEY = "test_api_key"
        mock_settings.DJSTRIPE_WEBHOOK_SECRET = "test_webhook_secret"
        mock_settings.STRIPE_LIVE_MODE = False

        mock_construct_event.side_effect = StripeError("API error")

        provider = StripePaymentProvider()
        payload = b'{"test": "payload"}'
        signature = "test_signature"

        # Execute & Verify
        with self.assertRaises(StripeError):
            provider.verify_webhook_signature(payload, signature)

    @mock.patch("order.payment.stripe.Webhook.construct_event")
    @mock.patch("order.payment.settings")
    def test_verify_webhook_signature_uses_correct_secret(
        self, mock_settings, mock_construct_event
    ):
        """
        Test that verification uses DJSTRIPE_WEBHOOK_SECRET from settings.

        Validates:
        - Correct webhook secret is passed to stripe.Webhook.construct_event
        - Secret is retrieved from settings.DJSTRIPE_WEBHOOK_SECRET
        """
        # Setup
        mock_settings.STRIPE_TEST_SECRET_KEY = "test_api_key"
        mock_settings.DJSTRIPE_WEBHOOK_SECRET = "whsec_test_secret_key_123"
        mock_settings.STRIPE_LIVE_MODE = False

        expected_event = {"id": "evt_test", "type": "test.event"}
        mock_construct_event.return_value = expected_event

        provider = StripePaymentProvider()
        payload = b'{"test": "payload"}'
        signature = "test_signature"

        # Execute
        provider.verify_webhook_signature(payload, signature)

        # Verify - check that the correct secret was used
        call_args = mock_construct_event.call_args
        self.assertEqual(call_args[0][2], "whsec_test_secret_key_123")


class StripePaymentIntentMetadataTestCase(TestCase):
    """
    Test enhanced payment intent creation with metadata and idempotency.
    """

    @mock.patch("order.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payment.PaymentIntent.sync_from_stripe_data")
    @mock.patch("order.payment.settings")
    def test_process_payment_with_comprehensive_metadata(
        self, mock_settings, mock_sync, mock_stripe_create
    ):
        """
        Test payment intent creation with comprehensive metadata.

        Validates:
        - Metadata includes order_id
        - Metadata includes cart_item_ids (comma-separated)
        - Metadata includes customer_email
        - All metadata fields are properly formatted
        """
        # Setup
        mock_settings.STRIPE_TEST_SECRET_KEY = "test_api_key"
        mock_settings.DJSTRIPE_WEBHOOK_SECRET = "test_webhook_secret"
        mock_settings.STRIPE_LIVE_MODE = False

        mock_payment_intent = mock.Mock()
        mock_payment_intent.id = "pi_test_123"
        mock_payment_intent.status = "requires_payment_method"
        mock_payment_intent.client_secret = "pi_test_123_secret"
        mock_payment_intent.next_action = None
        mock_stripe_create.return_value = mock_payment_intent

        provider = StripePaymentProvider()
        amount = Money(amount=Decimal("100.00"), currency="EUR")
        order_id = "12345"

        # Execute with comprehensive metadata
        success, payment_data = provider.process_payment(
            amount,
            order_id,
            cart_item_ids=[101, 102, 103],
            customer_email="customer@example.com",
        )

        # Verify success
        self.assertTrue(success)
        self.assertEqual(payment_data["payment_id"], "pi_test_123")

        # Verify metadata was passed correctly
        call_kwargs = mock_stripe_create.call_args[1]
        metadata = call_kwargs["metadata"]

        self.assertEqual(metadata["order_id"], "12345")
        self.assertEqual(metadata["cart_item_ids"], "101,102,103")
        self.assertEqual(metadata["customer_email"], "customer@example.com")
        self.assertEqual(metadata["source"], "django_app")

    @mock.patch("order.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payment.PaymentIntent.sync_from_stripe_data")
    @mock.patch("order.payment.settings")
    def test_process_payment_with_idempotency_key(
        self, mock_settings, mock_sync, mock_stripe_create
    ):
        """
        Test payment intent creation with idempotency key.

        Validates:
        - Idempotency key is generated from order_uuid
        - Idempotency key format is "order_{uuid}"
        - Idempotency key is passed to Stripe API
        """
        # Setup
        mock_settings.STRIPE_TEST_SECRET_KEY = "test_api_key"
        mock_settings.DJSTRIPE_WEBHOOK_SECRET = "test_webhook_secret"
        mock_settings.STRIPE_LIVE_MODE = False

        mock_payment_intent = mock.Mock()
        mock_payment_intent.id = "pi_test_123"
        mock_payment_intent.status = "requires_payment_method"
        mock_payment_intent.client_secret = "pi_test_123_secret"
        mock_payment_intent.next_action = None
        mock_stripe_create.return_value = mock_payment_intent

        provider = StripePaymentProvider()
        amount = Money(amount=Decimal("100.00"), currency="EUR")
        order_id = "12345"
        order_uuid = "550e8400-e29b-41d4-a716-446655440000"

        # Execute with order_uuid
        success, payment_data = provider.process_payment(
            amount, order_id, order_uuid=order_uuid
        )

        # Verify success
        self.assertTrue(success)

        # Verify idempotency key was passed
        call_kwargs = mock_stripe_create.call_args[1]
        self.assertEqual(
            call_kwargs["idempotency_key"],
            f"order_{order_uuid}",
        )

    @mock.patch("order.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payment.PaymentIntent.sync_from_stripe_data")
    @mock.patch("order.payment.settings")
    def test_process_payment_without_idempotency_key(
        self, mock_settings, mock_sync, mock_stripe_create
    ):
        """
        Test payment intent creation without idempotency key.

        Validates:
        - Payment intent can be created without order_uuid
        - No idempotency_key parameter is passed when order_uuid is absent
        - Backward compatibility is maintained
        """
        # Setup
        mock_settings.STRIPE_TEST_SECRET_KEY = "test_api_key"
        mock_settings.DJSTRIPE_WEBHOOK_SECRET = "test_webhook_secret"
        mock_settings.STRIPE_LIVE_MODE = False

        mock_payment_intent = mock.Mock()
        mock_payment_intent.id = "pi_test_123"
        mock_payment_intent.status = "requires_payment_method"
        mock_payment_intent.client_secret = "pi_test_123_secret"
        mock_payment_intent.next_action = None
        mock_stripe_create.return_value = mock_payment_intent

        provider = StripePaymentProvider()
        amount = Money(amount=Decimal("100.00"), currency="EUR")
        order_id = "12345"

        # Execute without order_uuid
        success, payment_data = provider.process_payment(amount, order_id)

        # Verify success
        self.assertTrue(success)

        # Verify idempotency_key was NOT passed
        call_kwargs = mock_stripe_create.call_args[1]
        self.assertNotIn("idempotency_key", call_kwargs)

    @mock.patch("order.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payment.PaymentIntent.sync_from_stripe_data")
    @mock.patch("order.payment.settings")
    def test_process_payment_with_cart_item_ids_as_string(
        self, mock_settings, mock_sync, mock_stripe_create
    ):
        """
        Test payment intent with cart_item_ids provided as string.

        Validates:
        - cart_item_ids can be provided as string
        - String is stored directly in metadata
        """
        # Setup
        mock_settings.STRIPE_TEST_SECRET_KEY = "test_api_key"
        mock_settings.DJSTRIPE_WEBHOOK_SECRET = "test_webhook_secret"
        mock_settings.STRIPE_LIVE_MODE = False

        mock_payment_intent = mock.Mock()
        mock_payment_intent.id = "pi_test_123"
        mock_payment_intent.status = "requires_payment_method"
        mock_payment_intent.client_secret = "pi_test_123_secret"
        mock_payment_intent.next_action = None
        mock_stripe_create.return_value = mock_payment_intent

        provider = StripePaymentProvider()
        amount = Money(amount=Decimal("100.00"), currency="EUR")
        order_id = "12345"

        # Execute with cart_item_ids as string
        success, payment_data = provider.process_payment(
            amount, order_id, cart_item_ids="101,102,103"
        )

        # Verify metadata
        call_kwargs = mock_stripe_create.call_args[1]
        metadata = call_kwargs["metadata"]
        self.assertEqual(metadata["cart_item_ids"], "101,102,103")

    @mock.patch("order.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payment.PaymentIntent.sync_from_stripe_data")
    @mock.patch("order.payment.settings")
    def test_process_payment_without_optional_metadata(
        self, mock_settings, mock_sync, mock_stripe_create
    ):
        """
        Test payment intent creation without optional metadata fields.

        Validates:
        - Payment intent can be created without cart_item_ids
        - Payment intent can be created without customer_email
        - Only required metadata (order_id, source) is included
        - Backward compatibility is maintained
        """
        # Setup
        mock_settings.STRIPE_TEST_SECRET_KEY = "test_api_key"
        mock_settings.DJSTRIPE_WEBHOOK_SECRET = "test_webhook_secret"
        mock_settings.STRIPE_LIVE_MODE = False

        mock_payment_intent = mock.Mock()
        mock_payment_intent.id = "pi_test_123"
        mock_payment_intent.status = "requires_payment_method"
        mock_payment_intent.client_secret = "pi_test_123_secret"
        mock_payment_intent.next_action = None
        mock_stripe_create.return_value = mock_payment_intent

        provider = StripePaymentProvider()
        amount = Money(amount=Decimal("100.00"), currency="EUR")
        order_id = "12345"

        # Execute without optional metadata
        success, payment_data = provider.process_payment(amount, order_id)

        # Verify success
        self.assertTrue(success)

        # Verify only required metadata is present
        call_kwargs = mock_stripe_create.call_args[1]
        metadata = call_kwargs["metadata"]

        self.assertEqual(metadata["order_id"], "12345")
        self.assertEqual(metadata["source"], "django_app")
        self.assertNotIn("cart_item_ids", metadata)
        self.assertNotIn("customer_email", metadata)

    @mock.patch("order.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payment.PaymentIntent.sync_from_stripe_data")
    @mock.patch("order.payment.settings")
    def test_process_payment_with_all_parameters(
        self, mock_settings, mock_sync, mock_stripe_create
    ):
        """
        Test payment intent creation with all possible parameters.

        Validates:
        - All metadata fields are included
        - Idempotency key is set
        - Customer ID is set
        - All parameters work together correctly
        """
        # Setup
        mock_settings.STRIPE_TEST_SECRET_KEY = "test_api_key"
        mock_settings.DJSTRIPE_WEBHOOK_SECRET = "test_webhook_secret"
        mock_settings.STRIPE_LIVE_MODE = False

        mock_payment_intent = mock.Mock()
        mock_payment_intent.id = "pi_test_123"
        mock_payment_intent.status = "requires_payment_method"
        mock_payment_intent.client_secret = "pi_test_123_secret"
        mock_payment_intent.next_action = None
        mock_stripe_create.return_value = mock_payment_intent

        provider = StripePaymentProvider()
        amount = Money(amount=Decimal("100.00"), currency="EUR")
        order_id = "12345"
        order_uuid = "550e8400-e29b-41d4-a716-446655440000"

        # Execute with all parameters
        success, payment_data = provider.process_payment(
            amount,
            order_id,
            order_uuid=order_uuid,
            cart_item_ids=[101, 102, 103],
            customer_email="customer@example.com",
            customer_id="cus_test_123",
        )

        # Verify success
        self.assertTrue(success)

        # Verify all parameters were passed correctly
        call_kwargs = mock_stripe_create.call_args[1]

        # Check metadata
        metadata = call_kwargs["metadata"]
        self.assertEqual(metadata["order_id"], "12345")
        self.assertEqual(metadata["cart_item_ids"], "101,102,103")
        self.assertEqual(metadata["customer_email"], "customer@example.com")
        self.assertEqual(metadata["source"], "django_app")

        # Check idempotency key
        self.assertEqual(call_kwargs["idempotency_key"], f"order_{order_uuid}")

        # Check customer ID
        self.assertEqual(call_kwargs["customer"], "cus_test_123")

    @mock.patch("order.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payment.PaymentIntent.sync_from_stripe_data")
    @mock.patch("order.payment.settings")
    def test_process_payment_with_empty_cart_item_ids_list(
        self, mock_settings, mock_sync, mock_stripe_create
    ):
        """
        Test payment intent with empty cart_item_ids list.

        Validates:
        - Empty list is converted to empty string
        - Payment intent creation succeeds
        """
        # Setup
        mock_settings.STRIPE_TEST_SECRET_KEY = "test_api_key"
        mock_settings.DJSTRIPE_WEBHOOK_SECRET = "test_webhook_secret"
        mock_settings.STRIPE_LIVE_MODE = False

        mock_payment_intent = mock.Mock()
        mock_payment_intent.id = "pi_test_123"
        mock_payment_intent.status = "requires_payment_method"
        mock_payment_intent.client_secret = "pi_test_123_secret"
        mock_payment_intent.next_action = None
        mock_stripe_create.return_value = mock_payment_intent

        provider = StripePaymentProvider()
        amount = Money(amount=Decimal("100.00"), currency="EUR")
        order_id = "12345"

        # Execute with empty cart_item_ids list
        success, payment_data = provider.process_payment(
            amount, order_id, cart_item_ids=[]
        )

        # Verify success
        self.assertTrue(success)

        # Verify empty string in metadata
        call_kwargs = mock_stripe_create.call_args[1]
        metadata = call_kwargs["metadata"]
        self.assertEqual(metadata["cart_item_ids"], "")
