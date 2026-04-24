from decimal import Decimal
from unittest import TestCase, mock

from django.conf import settings
from djmoney.money import Money

from order.payment import StripePaymentProvider


class StripePaymentAmountTestCase(TestCase):
    """Test that Stripe receives correct payment amounts."""

    @mock.patch("order.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payment.logger")
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

        with mock.patch("order.payment.PaymentIntent.sync_from_stripe_data"):
            provider = StripePaymentProvider()
            amount = Money(Decimal("10.50"), settings.DEFAULT_CURRENCY)
            provider.process_payment(amount, "order_1")

            call_kwargs = mock_stripe_create.call_args[1]
            self.assertEqual(call_kwargs["amount"], 1050)

    @mock.patch("order.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payment.logger")
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

        with mock.patch("order.payment.PaymentIntent.sync_from_stripe_data"):
            provider = StripePaymentProvider()
            amount = Money(Decimal("0.65"), settings.DEFAULT_CURRENCY)
            success, data = provider.process_payment(amount, "order_1")

            self.assertTrue(success)
            call_kwargs = mock_stripe_create.call_args[1]
            self.assertEqual(call_kwargs["amount"], 65)

    @mock.patch("order.payment.stripe.PaymentIntent.create")
    @mock.patch("order.payment.logger")
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
        success, data = provider.process_payment(amount, "order_1")

        self.assertFalse(success)
        self.assertIn("error", data)

    @mock.patch("order.payment.stripe.checkout.Session.create")
    @mock.patch("order.payment.logger")
    def test_checkout_session_splits_shipping_from_total(
        self, mock_logger, mock_session_create
    ):
        """Stripe checkout shows shipping as a separate line.
        Line item = total - shipping. Shipping = shipping_options."""
        mock_session = mock.Mock()
        mock_session.id = "cs_test"
        mock_session.url = "https://checkout.stripe.com/test"
        mock_session_create.return_value = mock_session

        provider = StripePaymentProvider()
        # Full order total: items (€25) + shipping (€3) + fee (€2) = €30
        total = Money(Decimal("30.00"), settings.DEFAULT_CURRENCY)
        shipping = Money(Decimal("3.00"), settings.DEFAULT_CURRENCY)

        success, data = provider.create_checkout_session(
            total,
            "order_1",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
            shipping_price=shipping,
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

    @mock.patch("order.payment.stripe.checkout.Session.create")
    @mock.patch("order.payment.logger")
    def test_checkout_session_no_shipping_when_free(
        self, mock_logger, mock_session_create
    ):
        """When shipping is zero (free shipping), no shipping_options
        are added and the full amount is the line item."""
        mock_session = mock.Mock()
        mock_session.id = "cs_test"
        mock_session.url = "https://checkout.stripe.com/test"
        mock_session_create.return_value = mock_session

        provider = StripePaymentProvider()
        total = Money(Decimal("55.00"), settings.DEFAULT_CURRENCY)
        shipping = Money(Decimal("0"), settings.DEFAULT_CURRENCY)

        success, data = provider.create_checkout_session(
            total,
            "order_1",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
            shipping_price=shipping,
        )

        self.assertTrue(success)
        call_kwargs = mock_session_create.call_args[1]
        line_item = call_kwargs["line_items"][0]
        self.assertEqual(line_item["price_data"]["unit_amount"], 5500)
        self.assertNotIn("shipping_options", call_kwargs)


class ShippingCostCalculationTestCase(TestCase):
    """Test shipping cost calculation used in payment intent creation."""

    @mock.patch("extra_settings.models.Setting.get")
    def test_shipping_included_below_threshold(self, mock_setting):
        """Orders below free shipping threshold should include shipping."""
        from order.services import OrderService

        mock_setting.side_effect = lambda key, **kw: {
            "CHECKOUT_SHIPPING_PRICE": 3.00,
            "FREE_SHIPPING_THRESHOLD": 50.00,
        }.get(key, kw.get("default"))

        cart_total = Money(Decimal("25.00"), "EUR")
        shipping = OrderService.calculate_shipping_cost(cart_total)
        self.assertEqual(shipping.amount, Decimal("3.00"))

    @mock.patch("extra_settings.models.Setting.get")
    def test_free_shipping_above_threshold(self, mock_setting):
        """Orders above free shipping threshold get free shipping."""
        from order.services import OrderService

        mock_setting.side_effect = lambda key, **kw: {
            "CHECKOUT_SHIPPING_PRICE": 3.00,
            "FREE_SHIPPING_THRESHOLD": 50.00,
        }.get(key, kw.get("default"))

        cart_total = Money(Decimal("60.00"), "EUR")
        shipping = OrderService.calculate_shipping_cost(cart_total)
        self.assertEqual(shipping.amount, Decimal("0"))

    def test_payment_method_fee_below_threshold(self):
        """Payment method fee is charged when order is below threshold."""
        from order.services import OrderService

        pay_way = mock.Mock()
        pay_way.cost = Money(Decimal("2.00"), "EUR")
        pay_way.free_threshold = Money(Decimal("100.00"), "EUR")

        order_value = Money(Decimal("30.00"), "EUR")
        fee = OrderService.calculate_payment_method_fee(pay_way, order_value)
        self.assertEqual(fee.amount, Decimal("2.00"))

    def test_payment_method_fee_free_above_threshold(self):
        """Payment method fee is waived above threshold."""
        from order.services import OrderService

        pay_way = mock.Mock()
        pay_way.cost = Money(Decimal("2.00"), "EUR")
        pay_way.free_threshold = Money(Decimal("100.00"), "EUR")

        order_value = Money(Decimal("150.00"), "EUR")
        fee = OrderService.calculate_payment_method_fee(pay_way, order_value)
        self.assertEqual(fee.amount, Decimal("0"))

    @mock.patch("extra_settings.models.Setting.get")
    def test_total_payment_amount_includes_all_components(self, mock_setting):
        """The total sent to Stripe must equal items + shipping + fee."""
        from order.services import OrderService

        mock_setting.side_effect = lambda key, **kw: {
            "CHECKOUT_SHIPPING_PRICE": 3.00,
            "FREE_SHIPPING_THRESHOLD": 50.00,
        }.get(key, kw.get("default"))

        items_total = Money(Decimal("25.00"), "EUR")
        shipping = OrderService.calculate_shipping_cost(items_total)

        pay_way = mock.Mock()
        pay_way.cost = Money(Decimal("1.50"), "EUR")
        pay_way.free_threshold = Money(Decimal("100.00"), "EUR")

        subtotal = Money(items_total.amount + shipping.amount, "EUR")
        fee = OrderService.calculate_payment_method_fee(pay_way, subtotal)

        total = Money(subtotal.amount + fee.amount, "EUR")

        # €25 items + €3 shipping + €1.50 fee = €29.50
        self.assertEqual(total.amount, Decimal("29.50"))

    @mock.patch("extra_settings.models.Setting.get")
    def test_small_order_with_shipping_above_stripe_minimum(self, mock_setting):
        """€0.25 product + €0.40 shipping = €0.65, above Stripe minimum."""
        from order.services import OrderService

        mock_setting.side_effect = lambda key, **kw: {
            "CHECKOUT_SHIPPING_PRICE": 0.40,
            "FREE_SHIPPING_THRESHOLD": 50.00,
        }.get(key, kw.get("default"))

        items_total = Money(Decimal("0.25"), "EUR")
        shipping = OrderService.calculate_shipping_cost(items_total)
        self.assertEqual(shipping.amount, Decimal("0.40"))

        total = Money(items_total.amount + shipping.amount, "EUR")
        # €0.25 + €0.40 = €0.65, above Stripe €0.50 minimum
        self.assertEqual(total.amount, Decimal("0.65"))
        self.assertGreaterEqual(total.amount, Decimal("0.50"))
