"""The provider is asked about the payment intent OUTSIDE the order
transaction, and the transaction re-checks the part that needs server state.

``create_order_from_cart`` used to call ``provider.get_payment_status``
(``PaymentIntent.retrieve`` plus the dj-stripe sync, an HTTP round trip)
inside its ``transaction.atomic``, after it had locked the cart and every
live automatic ``Promotion`` row. Each concurrent checkout evaluates those
promotions, so one slow answer from Stripe held all of them (Viva and cash
on delivery included) for as long as it took, and the connection sat idle
in a transaction that ``idle_in_transaction_session_timeout`` kills.

Tests prove each step by the statements it issues: a ``FOR UPDATE`` before
the provider call is a lock held across it.
"""

from unittest.mock import Mock, patch

import pytest
from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from djmoney.money import Money

from cart.models.cart import Cart
from cart.models.item import CartItem
from order.enum.status import PaymentStatus
from order.exceptions import (
    PaymentAmountMismatchError,
    PaymentCurrencyMismatchError,
)
from order.models.order import Order
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from product.models.product import Product
from tests.utils.legal import checkout_acceptance
from user.factories.account import UserAccountFactory

pytestmark = [
    pytest.mark.assert_english,
    pytest.mark.usefixtures("trust_intent_binding"),
]


def _eur(amount: str) -> Money:
    return Money(amount, settings.DEFAULT_CURRENCY)


@pytest.mark.django_db
class TestIntentIsReadOutsideTheTransaction:
    def setup_method(self):
        from country.factories import CountryFactory
        from tests.utils.shipping import enable_rate

        self.user = UserAccountFactory.create(num_addresses=0)
        self.pay_way = PayWayFactory.create(
            provider_code="stripe", settlement=PaySettlement.ONLINE
        )
        self.country = CountryFactory.create()
        enable_rate(self.country)
        self.product = ProductFactory.create(
            price=_eur("50.00"), stock=20, active=True
        )
        self.shipping_address = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "street": "Main St",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": self.country.alpha_2,
            "phone": "+306900000000",
            "shipping_kind": "home_delivery",
        }
        self.cart = self._cart()

    def _cart(self) -> Cart:
        cart = Cart.objects.create(user=None)
        CartItem.objects.create(cart=cart, product=self.product, quantity=2)
        return cart

    def _create(self, payment_intent_id, *, cart=None):
        return OrderService.create_order_from_cart(
            legal_acceptance=checkout_acceptance(),
            cart=cart or self.cart,
            shipping_address=self.shipping_address,
            payment_intent_id=payment_intent_id,
            pay_way=self.pay_way,
            user=None,
        )

    @staticmethod
    def _provider(read) -> Mock:
        provider = Mock()
        provider.get_payment_status.side_effect = read
        return provider

    @patch("order.payment.get_payment_provider")
    def test_no_row_is_locked_before_the_provider_answers(
        self, mock_get_provider
    ):
        seen: dict = {}
        baseline_depth = len(connection.savepoint_ids)

        def read(payment_id):
            seen["before"] = [q["sql"] for q in capture.captured_queries]
            # An ``atomic`` block nested in the test's own transaction is a
            # savepoint: none may be open while the provider is called.
            seen["depth"] = len(connection.savepoint_ids)
            return PaymentStatus.PENDING, {}

        mock_get_provider.return_value = self._provider(read)

        with CaptureQueriesContext(connection) as capture:
            self._create("pi_outside")

        assert "before" in seen, "the provider was never asked"
        assert not [sql for sql in seen["before"] if "FOR UPDATE" in sql], (
            "a FOR UPDATE ran before the provider call, so the row it "
            "locks is held across the provider's HTTP round trip: "
            f"{seen['before']!r}"
        )
        assert seen["depth"] == baseline_depth, (
            "the provider was called inside an atomic block"
        )
        after = [
            q["sql"] for q in capture.captured_queries[len(seen["before"]) :]
        ]
        assert [sql for sql in after if "FOR UPDATE" in sql], (
            "nothing was locked after the provider call: the test would "
            "pass for an order creation that locks nothing"
        )

    @patch("order.payment.get_payment_provider")
    def test_an_intent_matching_the_server_total_is_accepted(
        self, mock_get_provider
    ):
        mock_get_provider.return_value = self._provider(
            lambda _id: (PaymentStatus.PENDING, {})
        )
        reference = self._create("pi_reference")
        total = float(reference.amount_due.amount)

        mock_get_provider.return_value = self._provider(
            lambda _id: (
                PaymentStatus.PENDING,
                {"amount": total, "currency": "EUR"},
            )
        )
        order = self._create("pi_matching", cart=self._cart())

        assert order.payment_id == "pi_matching"
        assert order.amount_due == reference.amount_due

    @patch("order.payment.get_payment_provider")
    def test_the_server_total_is_recomputed_inside_the_transaction(
        self, mock_get_provider
    ):
        """The intent is read, then the price moves, then the order is
        built: the amount the provider reported was right when it was read
        and wrong by the time the order is priced. Only a comparison made
        under the lock, against the total computed there, catches it."""
        mock_get_provider.return_value = self._provider(
            lambda _id: (PaymentStatus.PENDING, {})
        )
        total = float(self._create("pi_reference").amount_due.amount)

        def read(payment_id):
            Product.objects.filter(pk=self.product.pk).update(
                price=_eur("60.00")
            )
            return PaymentStatus.PENDING, {"amount": total, "currency": "EUR"}

        mock_get_provider.return_value = self._provider(read)
        stock_before = Product.objects.get(pk=self.product.pk).stock

        with pytest.raises(PaymentAmountMismatchError):
            self._create("pi_stale", cart=self._cart())

        assert not Order.objects.filter(payment_id="pi_stale").exists()
        assert Product.objects.get(pk=self.product.pk).stock == stock_before, (
            "a refused order must not keep the stock it took"
        )

    @patch("order.payment.get_payment_provider")
    def test_the_intent_currency_is_rechecked_inside_the_transaction(
        self, mock_get_provider
    ):
        mock_get_provider.return_value = self._provider(
            lambda _id: (PaymentStatus.PENDING, {"currency": "USD"})
        )

        with pytest.raises(PaymentCurrencyMismatchError):
            self._create("pi_dollars")

        assert not Order.objects.filter(payment_id="pi_dollars").exists()
