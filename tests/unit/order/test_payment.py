from decimal import Decimal
from unittest import TestCase, mock

from django.conf import settings
from djmoney.money import Money
from djstripe.models import PaymentIntent

from order.enum.status import PaymentStatus
from order.payments.payment import (
    StripePaymentProvider,
    VivaWalletPaymentProvider,
    get_payment_provider,
    money_from_minor_units,
)
from order.payments.payment_binding import PaymentPurpose


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

    @mock.patch("order.payments.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payments.payment.logger")
    def test_stripe_process_payment(self, mock_logger, mock_stripe_create):
        mock_payment_intent = mock.Mock()
        mock_payment_intent.id = "pi_test_order_id_mock"
        mock_payment_intent.status = "succeeded"
        mock_payment_intent.client_secret = "pi_test_secret"
        mock_payment_intent.next_action = None
        mock_stripe_create.return_value = mock_payment_intent

        with mock.patch(
            "order.payments.payment.PaymentIntent.sync_from_stripe_data"
        ):
            provider = StripePaymentProvider()
            amount = Money(
                amount=Decimal("100.00"), currency=settings.DEFAULT_CURRENCY
            )
            order_id = "test_order_id"

            success, payment_data = provider.process_payment(
                amount,
                order_id,
                order_uuid="giftcard_order-uuid",
                customer_email="customer@example.com",
                purpose=PaymentPurpose.GIFT_CARD,
                subject="order-uuid",
            )

            self.assertTrue(success)
            self.assertEqual(
                payment_data["payment_id"], "pi_test_order_id_mock"
            )
            self.assertEqual(payment_data["status"], PaymentStatus.COMPLETED)

    @mock.patch("order.payments.payment.stripe.Refund.create")
    @mock.patch("order.payments.payment.logger")
    def test_stripe_refund_payment(self, mock_logger, mock_stripe_refund):
        mock_refund = mock.Mock()
        mock_refund.id = "re_test_payment_id_mock"
        mock_refund.status = "succeeded"
        mock_refund.amount = 5000
        mock_refund.currency = "eur"
        mock_stripe_refund.return_value = mock_refund

        with mock.patch("order.payments.payment.Refund.sync_from_stripe_data"):
            provider = StripePaymentProvider()
            payment_id = "test_payment_id"
            amount = Money(
                amount=Decimal("50.00"), currency=settings.DEFAULT_CURRENCY
            )

            success, refund_data = provider.refund_payment(
                payment_id, amount, idempotency_key="order-1-refund-1"
            )

            self.assertTrue(success)
            self.assertEqual(
                refund_data["refund_id"], "re_test_payment_id_mock"
            )
            # What Stripe paid back, in major units: 5000 minor units of EUR.
            self.assertEqual(Decimal(refund_data["amount"]), Decimal(50))
            # The key goes out as Stripe's Idempotency-Key: a retry under it
            # is answered with this refund instead of making another.
            self.assertEqual(
                mock_stripe_refund.call_args.kwargs["idempotency_key"],
                "order-1-refund-1",
            )

    @mock.patch("order.payments.payment.stripe.PaymentIntent.retrieve")
    @mock.patch("order.payments.payment.PaymentIntent.sync_from_stripe_data")
    @mock.patch("order.payments.payment.logger")
    def test_stripe_get_payment_status(
        self, mock_logger, mock_sync, mock_retrieve
    ):
        mock_payment_intent = mock.Mock()
        mock_payment_intent.id = "test_payment_id"
        mock_payment_intent.status = "succeeded"
        mock_payment_intent.amount = 10000
        mock_payment_intent.currency = "eur"
        mock_payment_intent.created = 1234567890
        mock_payment_intent.metadata = {}

        mock_retrieve.return_value = mock_payment_intent
        mock_djstripe_pi = mock.Mock()
        mock_djstripe_pi.created = "2024-01-01"
        mock_sync.return_value = mock_djstripe_pi

        with mock.patch(
            "order.payments.payment.PaymentIntent.objects.get",
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

        # A PayWay row can carry any provider_code an admin typed. One
        # this system cannot charge through must fail fast at resolution
        # rather than part-way through a checkout.
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
    """What a client-confirmed intent is created with: its metadata and
    its idempotency key (``StripePaymentProvider.process_payment``, the
    gift-card purchase's payment)."""

    ORDER_UUID = "giftcard_550e8400-e29b-41d4-a716-446655440000"

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

    def _create(self, mock_stripe_create, *, customer_email):
        mock_payment_intent = mock.Mock()
        mock_payment_intent.id = "pi_test_123"
        mock_payment_intent.status = "requires_payment_method"
        mock_payment_intent.client_secret = "pi_test_123_secret"
        mock_payment_intent.next_action = None
        mock_stripe_create.return_value = mock_payment_intent

        return StripePaymentProvider().process_payment(
            Money(amount=Decimal("100.00"), currency="EUR"),
            "12345",
            order_uuid=self.ORDER_UUID,
            customer_email=customer_email,
            purpose=PaymentPurpose.GIFT_CARD,
            subject="purchase-uuid",
        )

    @mock.patch("order.payments.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payments.payment.PaymentIntent.sync_from_stripe_data")
    def test_the_metadata_names_the_purchase_and_the_buyer(
        self, mock_sync, mock_stripe_create
    ):
        success, payment_data = self._create(
            mock_stripe_create, customer_email="customer@example.com"
        )

        self.assertTrue(success)
        self.assertEqual(payment_data["payment_id"], "pi_test_123")
        metadata = mock_stripe_create.call_args.kwargs["metadata"]
        self.assertEqual(metadata["order_id"], "12345")
        self.assertEqual(metadata["customer_email"], "customer@example.com")
        self.assertEqual(metadata["source"], "django_app")
        self.assertEqual(metadata["purpose"], PaymentPurpose.GIFT_CARD.value)
        self.assertEqual(metadata["subject"], "purchase-uuid")

    @mock.patch("order.payments.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payments.payment.PaymentIntent.sync_from_stripe_data")
    def test_the_intent_is_idempotent_on_the_purchase(
        self, mock_sync, mock_stripe_create
    ):
        success, _payment_data = self._create(
            mock_stripe_create, customer_email="customer@example.com"
        )

        self.assertTrue(success)
        self.assertEqual(
            mock_stripe_create.call_args.kwargs["idempotency_key"],
            f"order_{self.ORDER_UUID}",
        )

    @mock.patch("order.payments.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payments.payment.PaymentIntent.sync_from_stripe_data")
    def test_an_empty_email_is_left_out_of_the_metadata(
        self, mock_sync, mock_stripe_create
    ):
        success, _payment_data = self._create(
            mock_stripe_create, customer_email=""
        )

        self.assertTrue(success)
        metadata = mock_stripe_create.call_args.kwargs["metadata"]
        self.assertNotIn("customer_email", metadata)
        self.assertNotIn("customer", mock_stripe_create.call_args.kwargs)


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


class MinorUnitsTestCase(TestCase):
    """Stripe amounts are integers in the currency's OWN smallest unit.

    The exponent comes from the currency (``moneyed``), never from an
    assumed two decimals: 500 JPY is five hundred yen, not five.
    """

    def test_two_decimal_currency(self):
        self.assertEqual(
            money_from_minor_units(5000, "eur"), Money(Decimal(50), "EUR")
        )

    def test_zero_decimal_currency(self):
        self.assertEqual(
            money_from_minor_units(500, "jpy"), Money(Decimal(500), "JPY")
        )

    def test_three_decimal_currency(self):
        self.assertEqual(
            money_from_minor_units(5000, "BHD"), Money(Decimal(5), "BHD")
        )

    def test_stripe_refund_request_uses_the_currency_exponent(self):
        with (
            mock.patch("tenant.credentials.stripe_credentials") as creds,
            mock.patch("order.payments.payment.stripe.Refund.create") as create,
            mock.patch("order.payments.payment.Refund.sync_from_stripe_data"),
        ):
            creds.return_value = {"secret_key": "sk_test_dummy"}
            create.return_value = mock.Mock(
                id="re_1", status="succeeded", amount=500, currency="jpy"
            )
            success, data = StripePaymentProvider().refund_payment(
                "pi_1",
                Money(Decimal(500), "JPY"),
                idempotency_key="order-1-refund-1",
            )

        self.assertTrue(success)
        self.assertEqual(create.call_args.kwargs["amount"], 500)
        self.assertEqual(Decimal(data["amount"]), Decimal(500))

    def test_stripe_payment_status_amount_is_in_major_units(self):
        with (
            mock.patch("tenant.credentials.stripe_credentials") as creds,
            mock.patch(
                "order.payments.payment.stripe.PaymentIntent.retrieve"
            ) as get,
            mock.patch(
                "order.payments.payment.PaymentIntent.sync_from_stripe_data"
            ),
        ):
            creds.return_value = {"secret_key": "sk_test_dummy"}
            get.return_value = mock.Mock(
                status="succeeded",
                amount=500,
                currency="jpy",
                created=1,
                metadata={},
            )
            _status, data = StripePaymentProvider().get_payment_status("pi_1")

        self.assertEqual(Decimal(str(data["amount"])), Decimal(500))


class StripeRefundStatusTestCase(TestCase):
    """What a Stripe refund's ``status`` means for the order's money.

    Stripe answers ``succeeded`` for a partial refund exactly as for a full
    one (docs.stripe.com/api/refunds/object), so the status never says
    whether the order is fully refunded; the ledger's sums do. What it does
    say is whether Stripe took the refund on: ``succeeded``, and ``pending``
    (asynchronous methods; committed against the charge from then on).
    ``requires_action`` waits on the customer and ``failed``/``canceled``
    returned nothing, so none of those is a refund that was made.
    """

    def _refund(self, status):
        with (
            mock.patch("tenant.credentials.stripe_credentials") as creds,
            mock.patch("order.payments.payment.stripe.Refund.create") as create,
            mock.patch("order.payments.payment.Refund.sync_from_stripe_data"),
        ):
            creds.return_value = {"secret_key": "sk_test_dummy"}
            create.return_value = mock.Mock(
                id="re_1", status=status, amount=1500, currency="eur"
            )
            return StripePaymentProvider().refund_payment(
                "pi_1",
                Money(Decimal("15.00"), "EUR"),
                idempotency_key="order-1-refund-1",
            )

    def test_succeeded_and_pending_refunds_are_accepted(self):
        for status in ("succeeded", "pending"):
            with self.subTest(status=status):
                success, data = self._refund(status)

                self.assertTrue(success)
                self.assertEqual(data["refund_id"], "re_1")
                self.assertEqual(data["stripe_status"], status)
                self.assertEqual(Decimal(data["amount"]), Decimal("15.00"))

    def test_a_refund_stripe_did_not_take_on_is_not_a_refund(self):
        for status in ("requires_action", "failed", "canceled"):
            with self.subTest(status=status):
                success, data = self._refund(status)

                self.assertFalse(success)
                self.assertEqual(data["refund_id"], "re_1")
                self.assertEqual(data["stripe_status"], status)
                self.assertIn(status, data["error"])

    def test_the_provider_never_says_which_payment_status_follows(self):
        """Stripe says ``succeeded`` for a part refund too, so the answer
        carries no payment status: the ledger derives it."""
        _success, data = self._refund("succeeded")

        self.assertNotIn("status", data)
