from decimal import Decimal
from unittest import TestCase, mock

from django.conf import settings
from djmoney.money import Money
from djstripe.models import PaymentIntent

from order.enum.status import PaymentStatus
from order.payment import (
    PayPalPaymentProvider,
    StripePaymentProvider,
    VivaWalletPaymentProvider,
    get_payment_provider,
)


class PaymentModuleTestCase(TestCase):
    def setUp(self):
        # Deterministic Stripe identity for provider construction: these
        # tests run outside any tenant context (public schema), where
        # ``stripe_credentials()`` now has no fallback at all — tests
        # that only care about Stripe API call behaviour (not credential
        # resolution) need a stand-in tenant key. Tests that DO exercise
        # credential resolution re-patch this target explicitly.
        patcher = mock.patch(
            "tenant.credentials.stripe_credentials",
            return_value={
                "secret_key": "sk_test_dummy_tenant_key",
                "publishable_key": "pk_test_dummy_tenant_key",
                "live_mode": False,
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

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

    @mock.patch("tenant.credentials.stripe_credentials")
    def test_stripe_payment_provider_init(self, mock_creds):
        mock_creds.return_value = {
            "secret_key": "sk_test_tenant_key",
            "publishable_key": "pk_test_x",
            "live_mode": False,
        }

        provider = StripePaymentProvider()

        self.assertEqual(provider.api_key, "sk_test_tenant_key")

    @mock.patch("tenant.credentials.stripe_credentials")
    def test_stripe_payment_provider_init_unconfigured_raises(self, mock_creds):
        # No tenant key and no platform-account opt-in: constructing the
        # provider must fail loudly instead of silently charging the
        # platform account.
        from django.core.exceptions import ImproperlyConfigured

        mock_creds.return_value = {
            "secret_key": "",
            "publishable_key": "",
            "live_mode": False,
        }

        with self.assertRaises(ImproperlyConfigured):
            StripePaymentProvider()

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
    def test_paypal_process_payment_raises_not_implemented(self, mock_logger):
        """PayPal provider is intentionally a hard-stop: the previous mock
        ``COMPLETED`` return was an audit finding (any active PayPal pay-way
        would have auto-confirmed orders without a real payment).  Until a
        real PayPal integration ships, ``process_payment`` raises
        ``NotImplementedError`` so the failure surfaces immediately.
        """
        provider = PayPalPaymentProvider()
        amount = Money(
            amount=Decimal("100.00"), currency=settings.DEFAULT_CURRENCY
        )
        order_id = "test_order_id"

        with self.assertRaises(NotImplementedError):
            provider.process_payment(amount, order_id)

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

    @mock.patch("tenant.credentials.viva_wallet_credentials")
    def test_get_payment_provider(self, mock_viva_creds):
        # Viva Wallet has no settings fallback — mock tenant credentials
        # so resolving the provider by name doesn't raise
        # ImproperlyConfigured in this no-active-tenant test context.
        mock_viva_creds.return_value = {
            "merchant_id": "m1",
            "api_key": "k1",
            "client_id": "c1",
            "client_secret": "s1",
            "webhook_verification_key": "",
            "source_code": "",
            "live_mode": False,
        }

        provider = get_payment_provider("stripe")
        self.assertIsInstance(provider, StripePaymentProvider)

        provider = get_payment_provider("viva_wallet")
        self.assertIsInstance(provider, VivaWalletPaymentProvider)

        # PayPal is an unimplemented stub and deliberately unregistered —
        # resolving it must fail fast instead of exposing a provider whose
        # every method raises NotImplementedError mid-checkout.
        with self.assertRaises(ValueError):
            get_payment_provider("paypal")

        with self.assertRaises(ValueError):
            get_payment_provider("invalid_provider")

    @mock.patch("tenant.credentials.viva_wallet_credentials")
    def test_viva_wallet_payment_provider_init(self, mock_creds):
        mock_creds.return_value = {
            "merchant_id": "m1",
            "api_key": "k1",
            "client_id": "c1",
            "client_secret": "s1",
            "webhook_verification_key": "",
            "source_code": "SRC1",
            "live_mode": True,
        }

        provider = VivaWalletPaymentProvider()

        self.assertEqual(provider.merchant_id, "m1")
        self.assertEqual(provider.api_key, "k1")
        self.assertEqual(provider.client_id, "c1")
        self.assertEqual(provider.client_secret, "s1")
        self.assertEqual(provider.source_code, "SRC1")
        self.assertTrue(provider.live_mode)

    @mock.patch("tenant.credentials.viva_wallet_credentials")
    def test_viva_wallet_payment_provider_init_unconfigured_raises(
        self, mock_creds
    ):
        # No tenant OAuth2 credentials: constructing the provider must
        # fail loudly instead of silently proceeding with empty auth.
        from django.core.exceptions import ImproperlyConfigured

        mock_creds.return_value = {
            "merchant_id": "",
            "api_key": "",
            "client_id": "",
            "client_secret": "",
            "webhook_verification_key": "",
            "source_code": "",
            "live_mode": False,
        }

        with self.assertRaises(ImproperlyConfigured):
            VivaWalletPaymentProvider()


class StripePaymentIntentMetadataTestCase(TestCase):
    """
    Test enhanced payment intent creation with metadata and idempotency.
    """

    def setUp(self):
        # See PaymentModuleTestCase.setUp — these tests construct
        # StripePaymentProvider() outside any tenant context.
        patcher = mock.patch(
            "tenant.credentials.stripe_credentials",
            return_value={
                "secret_key": "sk_test_dummy_tenant_key",
                "publishable_key": "pk_test_dummy_tenant_key",
                "live_mode": False,
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

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


class StripeApiVersionConfigTestCase(TestCase):
    """dj-stripe must run on its own schema-matched API version.

    Pinning STRIPE_API_VERSION to an older value forces dj-stripe to parse
    payloads shaped for one API version against models built for another,
    silently corrupting the local Stripe mirror. dj-stripe's docs state the
    value "should not be changed", so the project must not define it.
    """

    def test_stripe_api_version_is_not_overridden(self):
        self.assertFalse(
            hasattr(settings, "STRIPE_API_VERSION"),
            "settings must not pin STRIPE_API_VERSION; dj-stripe manages it "
            "to keep the API schema aligned with its model schema.",
        )

    def test_djstripe_resolves_to_installed_sdk_version(self):
        import stripe
        from djstripe.settings import djstripe_settings

        # With no override, dj-stripe uses the installed SDK's api_version,
        # which tracks the dj-stripe model schema train (currently .dahlia).
        self.assertEqual(
            djstripe_settings.STRIPE_API_VERSION, stripe.api_version
        )
        self.assertNotEqual(djstripe_settings.STRIPE_API_VERSION, "2024-04-10")
