from decimal import Decimal
from unittest import TestCase, mock

import pytest
from django.conf import settings
from djmoney.money import Money

from order.payments.payment import StripePaymentProvider
from order.payments.payment_binding import PaymentPurpose


class StripePaymentAmountTestCase(TestCase):
    """Test that Stripe receives correct payment amounts."""

    def setUp(self):
        # These tests run outside any tenant context (public schema),
        # where stripe_credentials() has no fallback at all — provide a
        # stand-in tenant key so StripePaymentProvider() constructs.
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

    @mock.patch("order.payments.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payments.payment.logger")
    def test_process_payment_converts_to_cents(
        self, mock_logger, mock_stripe_create
    ):
        """Stripe amount must be in cents (€1.00 = 100)."""
        mock_pi = mock.Mock()
        mock_pi.id = "pi_test"
        mock_pi.status = "succeeded"
        mock_pi.client_secret = "secret"
        mock_pi.next_action = None
        mock_stripe_create.return_value = mock_pi

        with mock.patch(
            "order.payments.payment.PaymentIntent.sync_from_stripe_data"
        ):
            provider = StripePaymentProvider()
            amount = Money(Decimal("10.50"), settings.DEFAULT_CURRENCY)
            provider.process_payment(
                amount,
                "order_1",
                order_uuid="giftcard_order-uuid",
                customer_email="",
                purpose=PaymentPurpose.GIFT_CARD,
                subject="order-uuid",
            )

            call_kwargs = mock_stripe_create.call_args[1]
            self.assertEqual(call_kwargs["amount"], 1050)

    @mock.patch("order.payments.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payments.payment.logger")
    def test_process_payment_small_amount_above_minimum(
        self, mock_logger, mock_stripe_create
    ):
        """€0.65 (65 cents) is above Stripe minimum and should work."""
        mock_pi = mock.Mock()
        mock_pi.id = "pi_test"
        mock_pi.status = "succeeded"
        mock_pi.client_secret = "secret"
        mock_pi.next_action = None
        mock_stripe_create.return_value = mock_pi

        with mock.patch(
            "order.payments.payment.PaymentIntent.sync_from_stripe_data"
        ):
            provider = StripePaymentProvider()
            amount = Money(Decimal("0.65"), settings.DEFAULT_CURRENCY)
            success, _data = provider.process_payment(
                amount,
                "order_1",
                order_uuid="giftcard_order-uuid",
                customer_email="",
                purpose=PaymentPurpose.GIFT_CARD,
                subject="order-uuid",
            )

            self.assertTrue(success)
            call_kwargs = mock_stripe_create.call_args[1]
            self.assertEqual(call_kwargs["amount"], 65)

    @mock.patch("order.payments.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payments.payment.logger")
    def test_process_payment_below_stripe_minimum_fails(
        self, mock_logger, mock_stripe_create
    ):
        """€0.25 is below Stripe €0.50 minimum — Stripe rejects it."""
        from stripe._error import InvalidRequestError

        mock_stripe_create.side_effect = InvalidRequestError(
            message="Amount must be at least €0.50 eur",
            param="amount",
        )

        provider = StripePaymentProvider()
        amount = Money(Decimal("0.25"), settings.DEFAULT_CURRENCY)
        success, data = provider.process_payment(
            amount,
            "order_1",
            order_uuid="giftcard_order-uuid",
            customer_email="",
            purpose=PaymentPurpose.GIFT_CARD,
            subject="order-uuid",
        )

        self.assertFalse(success)
        self.assertIn("error", data)

    @mock.patch("order.payments.payment.stripe.checkout.Session.create")
    @mock.patch("order.payments.payment.logger")
    def test_checkout_session_splits_shipping_from_total(
        self, mock_logger, mock_session_create
    ):
        """Stripe checkout shows shipping as a separate line.
        Line item = total - shipping. Shipping = shipping_options."""
        mock_session = mock.Mock()
        mock_session.id = "cs_test"
        mock_session.url = "https://checkout.stripe.com/test"
        mock_session.expires_at = 1_800_000_000
        mock_session_create.return_value = mock_session

        provider = StripePaymentProvider()
        # Full order total: items (€25) + shipping (€3) + fee (€2) = €30
        total = Money(Decimal("30.00"), settings.DEFAULT_CURRENCY)
        shipping = Money(Decimal("3.00"), settings.DEFAULT_CURRENCY)

        success, _data = provider.create_checkout_session(
            total,
            "order_1",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
            shipping_price=shipping,
            purpose=PaymentPurpose.ORDER,
            subject="order-uuid",
        )

        self.assertTrue(success)
        call_kwargs = mock_session_create.call_args[1]

        # Line item = total - shipping = €27 (items + fee)
        line_item = call_kwargs["line_items"][0]
        self.assertEqual(line_item["price_data"]["unit_amount"], 2700)

        # Shipping shown separately
        shipping_opt = call_kwargs["shipping_options"][0]
        self.assertEqual(
            shipping_opt["shipping_rate_data"]["fixed_amount"]["amount"],
            300,
        )

    @mock.patch("order.payments.payment.stripe.checkout.Session.create")
    @mock.patch("order.payments.payment.logger")
    def test_checkout_session_no_shipping_when_free(
        self, mock_logger, mock_session_create
    ):
        """When shipping is zero (free shipping), no shipping_options
        are added and the full amount is the line item."""
        mock_session = mock.Mock()
        mock_session.id = "cs_test"
        mock_session.url = "https://checkout.stripe.com/test"
        mock_session.expires_at = 1_800_000_000
        mock_session_create.return_value = mock_session

        provider = StripePaymentProvider()
        total = Money(Decimal("55.00"), settings.DEFAULT_CURRENCY)
        shipping = Money(Decimal(0), settings.DEFAULT_CURRENCY)

        success, _data = provider.create_checkout_session(
            total,
            "order_1",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
            shipping_price=shipping,
            purpose=PaymentPurpose.ORDER,
            subject="order-uuid",
        )

        self.assertTrue(success)
        call_kwargs = mock_session_create.call_args[1]
        line_item = call_kwargs["line_items"][0]
        self.assertEqual(line_item["price_data"]["unit_amount"], 5500)
        self.assertNotIn("shipping_options", call_kwargs)


class PaymentMethodFeeTestCase(TestCase):
    """``calculate_payment_method_fee`` is plain Money math over a
    ``pay_way`` — unaffected by the ``ShippingRate`` migration, so it
    stays a DB-less ``TestCase`` with a mocked pay-way."""

    def test_payment_method_fee_below_threshold(self):
        """Payment method fee is charged when order is below threshold."""
        from order.checkout.quote import OrderQuoteService

        pay_way = mock.Mock()
        pay_way.cost = Money(Decimal("2.00"), "EUR")
        pay_way.free_threshold = Money(Decimal("100.00"), "EUR")

        order_value = Money(Decimal("30.00"), "EUR")
        fee = OrderQuoteService.calculate_payment_method_fee(
            pay_way, order_value
        )
        self.assertEqual(fee.amount, Decimal("2.00"))

    def test_payment_method_fee_free_above_threshold(self):
        """Payment method fee is waived above threshold."""
        from order.checkout.quote import OrderQuoteService

        pay_way = mock.Mock()
        pay_way.cost = Money(Decimal("2.00"), "EUR")
        pay_way.free_threshold = Money(Decimal("100.00"), "EUR")

        order_value = Money(Decimal("150.00"), "EUR")
        fee = OrderQuoteService.calculate_payment_method_fee(
            pay_way, order_value
        )
        self.assertEqual(fee.amount, Decimal(0))


@pytest.mark.django_db
class TestOrderQuoteServiceShippingCost:
    """``OrderQuoteService.shipping_cost`` — pricing now comes from a
    ``ShippingRate`` row, not a global Setting pair; ``ShippingService``
    itself (gate, quote order, weight cap) is covered by
    ``tests/unit/shipping/test_shipping_service_rates.py``. These tests pin what
    THIS wrapper adds: home-delivery auto-resolution, the ``free``
    override, and translating ``shipping.exceptions`` into
    ``InvalidOrderDataError`` so every caller gets one error shape.
    """

    def test_shipping_included_below_threshold(self):
        from country.factories import CountryFactory
        from order.checkout.quote import OrderQuoteService
        from tests.utils.shipping import enable_rate

        country = CountryFactory()
        enable_rate(
            country,
            provider_code="flat_rate",
            price=Decimal("3.00"),
            free_shipping_threshold=Decimal("50.00"),
        )

        shipping = OrderQuoteService.shipping_cost(
            order_value=Money(Decimal("25.00"), "EUR"),
            country_id=country.alpha_2,
            shipping_provider_code="flat_rate",
            shipping_kind="home_delivery",
        ).price
        assert shipping.amount == Decimal("3.00")

    def test_free_shipping_above_threshold(self):
        from country.factories import CountryFactory
        from order.checkout.quote import OrderQuoteService
        from tests.utils.shipping import enable_rate

        country = CountryFactory()
        enable_rate(
            country,
            provider_code="flat_rate",
            price=Decimal("3.00"),
            free_shipping_threshold=Decimal("50.00"),
        )

        shipping = OrderQuoteService.shipping_cost(
            order_value=Money(Decimal("60.00"), "EUR"),
            country_id=country.alpha_2,
            shipping_provider_code="flat_rate",
            shipping_kind="home_delivery",
        ).price
        assert shipping.amount == Decimal(0)

    def test_home_delivery_auto_resolves_the_active_provider(self):
        """No explicit ``shipping_provider_code`` — the lowest-priority
        active provider with a rate for the country wins."""
        from country.factories import CountryFactory
        from order.checkout.quote import OrderQuoteService
        from tests.utils.shipping import enable_rate

        country = CountryFactory()
        enable_rate(country, provider_code="flat_rate", price=Decimal("4.00"))

        shipping = OrderQuoteService.shipping_cost(
            order_value=Money(Decimal("10.00"), "EUR"),
            country_id=country.alpha_2,
            shipping_kind="home_delivery",
        ).price
        assert shipping.amount == Decimal("4.00")

    def test_free_true_forces_zero_but_still_checks_availability(self):
        from country.factories import CountryFactory
        from order.checkout.quote import OrderQuoteService
        from tests.utils.shipping import enable_rate

        country = CountryFactory()
        enable_rate(country, provider_code="flat_rate", price=Decimal("4.00"))

        shipping = OrderQuoteService.shipping_cost(
            order_value=Money(Decimal("10.00"), "EUR"),
            country_id=country.alpha_2,
            shipping_provider_code="flat_rate",
            shipping_kind="home_delivery",
            free=True,
        ).price
        assert shipping.amount == Decimal(0)

    def test_free_true_still_raises_when_unavailable(self):
        """A free-shipping promo cannot make an unavailable option
        orderable just because it charges nothing."""
        from country.factories import CountryFactory
        from order.checkout.quote import OrderQuoteService
        from order.exceptions import InvalidOrderDataError

        country = CountryFactory()
        with pytest.raises(InvalidOrderDataError):
            OrderQuoteService.shipping_cost(
                order_value=Money(Decimal("10.00"), "EUR"),
                country_id=country.alpha_2,
                shipping_provider_code="not_a_real_carrier",
                shipping_kind="home_delivery",
                free=True,
            )

    def test_raises_invalid_order_data_error_when_unavailable(self):
        from country.factories import CountryFactory
        from order.checkout.quote import OrderQuoteService
        from order.exceptions import InvalidOrderDataError

        country = CountryFactory()
        with pytest.raises(InvalidOrderDataError) as exc_info:
            OrderQuoteService.shipping_cost(
                order_value=Money(Decimal("10.00"), "EUR"),
                country_id=country.alpha_2,
                shipping_provider_code="not_a_real_carrier",
                shipping_kind="home_delivery",
            )
        assert "shipping_provider_code" in exc_info.value.field_errors

    def test_raises_invalid_order_data_error_over_the_weight_cap(self):
        from country.factories import CountryFactory
        from order.checkout.quote import OrderQuoteService
        from order.exceptions import InvalidOrderDataError
        from tests.utils.shipping import enable_rate

        country = CountryFactory()
        enable_rate(
            country,
            provider_code="flat_rate",
            price=Decimal("4.00"),
            max_weight_grams=4000,
        )

        with pytest.raises(InvalidOrderDataError):
            OrderQuoteService.shipping_cost(
                order_value=Money(Decimal("10.00"), "EUR"),
                country_id=country.alpha_2,
                shipping_provider_code="flat_rate",
                shipping_kind="home_delivery",
                weight_grams=5000,
            )

    def test_boxnow_shipping_ignores_country_region_adjustments(
        self, boxnow_configured_tenant
    ):
        """BoxNow's rate is flat per country — no separate region
        multiplier, so ``region_id`` doesn't change the price."""
        from country.factories import CountryFactory
        from order.checkout.quote import OrderQuoteService
        from shipping.enum import ShippingKind
        from shipping.models import ShippingProvider
        from tests.utils.shipping import enable_rate

        country = CountryFactory()
        boxnow = ShippingProvider.objects.get(code="boxnow")
        boxnow.is_active = True
        boxnow.save(update_fields=["is_active"])
        enable_rate(
            country,
            provider_code="boxnow",
            kind=ShippingKind.PICKUP_POINT,
            price=Decimal("2.50"),
            free_shipping_threshold=Decimal("30.00"),
        )

        shipping = OrderQuoteService.shipping_cost(
            order_value=Money(Decimal("25.00"), "EUR"),
            country_id=country.alpha_2,
            region_id="ignored-region",
            shipping_provider_code="boxnow",
            shipping_kind="pickup_point",
        ).price
        assert shipping.amount == Decimal("2.50")

    def test_total_payment_amount_includes_all_components(self):
        """The total sent to Stripe must equal items + shipping + fee."""
        from country.factories import CountryFactory
        from order.checkout.quote import OrderQuoteService
        from tests.utils.shipping import enable_rate

        country = CountryFactory()
        enable_rate(
            country,
            provider_code="flat_rate",
            price=Decimal("3.00"),
            free_shipping_threshold=Decimal("50.00"),
        )

        items_total = Money(Decimal("25.00"), "EUR")
        shipping = OrderQuoteService.shipping_cost(
            order_value=items_total,
            country_id=country.alpha_2,
            shipping_provider_code="flat_rate",
            shipping_kind="home_delivery",
        ).price

        pay_way = mock.Mock()
        pay_way.cost = Money(Decimal("1.50"), "EUR")
        pay_way.free_threshold = Money(Decimal("100.00"), "EUR")

        subtotal = Money(items_total.amount + shipping.amount, "EUR")
        fee = OrderQuoteService.calculate_payment_method_fee(pay_way, subtotal)

        total = Money(subtotal.amount + fee.amount, "EUR")

        # €25 items + €3 shipping + €1.50 fee = €29.50
        assert total.amount == Decimal("29.50")
