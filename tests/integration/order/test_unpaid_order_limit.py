"""How much stock one customer can hold with orders they never pay.

An online-payment order holds its stock until it is paid or
``auto_cancel_stuck_pending_orders`` cancels it. Nothing bounded how many
one customer could have waiting, so checkouts started and never paid
locked stock for as long as the auto-cancel window. The bound is a tenant
setting, ``ORDER_MAX_UNPAID_ONLINE_ORDERS``.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import InvalidOrderDataError, UnpaidOrderLimitError
from order.factories import OrderFactory
from order.models.order import Order
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from tests.utils.legal import checkout_acceptance
from tests.utils.shipping import enable_rate
from user.factories import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

SETTING = "ORDER_MAX_UNPAID_ONLINE_ORDERS"


def _limit(value):
    return patch(
        "extra_settings.models.Setting.get",
        side_effect=lambda key, default=None: (
            value if key == SETTING else default
        ),
    )


def _online():
    return PayWayFactory.create_online_payment("viva_wallet")


def _cod():
    return PayWayFactory(
        settlement=PaySettlement.COURIER_CASH,
        active=True,
        cost=Money(Decimal(0), "EUR"),
        free_threshold=Money(Decimal(0), "EUR"),
    )


def _waiting(*, email="maria@example.com", user=None, pay_way=None, **kwargs):
    kwargs.setdefault("status", OrderStatus.PENDING)
    kwargs.setdefault("payment_status", PaymentStatus.PENDING)
    return OrderFactory(
        num_order_items=0,
        email=email,
        user=user,
        pay_way=pay_way or _online(),
        **kwargs,
    )


def _place(*, email="maria@example.com", user=None, pay_way=None):
    cart = CartFactory(is_guest=user is None, user=user)
    cart.items.all().delete()
    CartItemFactory(
        cart=cart,
        product=ProductFactory(
            stock=10,
            price=Money(Decimal("10.00"), "EUR"),
            discount_percent=Decimal(0),
            active=True,
        ),
        quantity=1,
    )
    country = CountryFactory()
    enable_rate(country)
    return OrderService.create_order_from_cart_offline(
        legal_acceptance=checkout_acceptance(),
        cart=cart,
        shipping_address={
            "first_name": "Maria",
            "last_name": "Papadopoulou",
            "email": email,
            "street": "Ermou",
            "street_number": "1",
            "city": "Athens",
            "zipcode": "10563",
            "country_id": country.alpha_2,
            "phone": "+306900000000",
            "shipping_kind": "home_delivery",
        },
        pay_way=pay_way or _online(),
        user=user,
    )


class TestAGuestIsBoundedByEmail:
    def test_the_order_over_the_limit_is_refused(self):
        for _ in range(2):
            _waiting()

        with _limit(2), pytest.raises(UnpaidOrderLimitError) as excinfo:
            _place()

        assert excinfo.value.limit == 2
        # Callers that catch InvalidOrderDataError still catch it.
        assert isinstance(excinfo.value, InvalidOrderDataError)
        assert Order.objects.filter(email="maria@example.com").count() == 2

    def test_an_order_under_the_limit_is_placed(self):
        _waiting()

        with _limit(2):
            order = _place()

        assert order.status == OrderStatus.PENDING

    def test_the_email_is_compared_without_case(self):
        _waiting(email="Maria@Example.com")

        with _limit(1), pytest.raises(InvalidOrderDataError):
            _place(email="maria@example.COM")

    def test_another_customer_is_not_counted(self):
        for _ in range(3):
            _waiting(email="someone@else.example")

        with _limit(2):
            assert _place().status == OrderStatus.PENDING


class TestAnAccountIsBoundedByTheAccount:
    def test_the_account_is_counted_whatever_the_email(self):
        user = UserAccountFactory(num_addresses=0)
        for n in range(2):
            _waiting(email=f"alias{n}@example.com", user=user)

        with _limit(2), pytest.raises(InvalidOrderDataError):
            _place(user=user)

    def test_a_guest_order_on_the_same_email_is_not_the_accounts(self):
        user = UserAccountFactory(num_addresses=0)
        _waiting(user=None)

        with _limit(1):
            assert _place(user=user).status == OrderStatus.PENDING


class TestOnlyWhatIsStillWaitingCounts:
    @pytest.mark.parametrize(
        "state",
        [
            {"status": OrderStatus.CANCELED},
            {"payment_status": PaymentStatus.COMPLETED},
            {"status": OrderStatus.PROCESSING},
            # Settled payments on a PENDING order: nothing is owed.
            {"payment_status": PaymentStatus.CANCELED},
            {"payment_status": PaymentStatus.REFUNDED},
            {"payment_status": PaymentStatus.PARTIALLY_REFUNDED},
        ],
    )
    def test_a_paid_canceled_or_processing_order_does_not(self, state):
        _waiting(**state)

        with _limit(1):
            assert _place().status == OrderStatus.PENDING

    @pytest.mark.parametrize(
        "payment_status", [PaymentStatus.FAILED, PaymentStatus.PROCESSING]
    )
    def test_a_failed_or_processing_payment_still_counts(self, payment_status):
        """A PENDING order whose payment failed keeps its stock until it is
        paid or the sweep cancels it; one whose payment is processing keeps
        it until its final webhook lands or the customer cancels it. Either
        is still waiting for its payment."""
        _waiting(payment_status=payment_status)

        with _limit(1), pytest.raises(InvalidOrderDataError):
            _place()

    def test_an_offline_order_does_not(self):
        _waiting(pay_way=_cod())

        with _limit(1):
            assert _place().status == OrderStatus.PENDING

    def test_paying_an_offline_method_is_never_limited(self):
        for _ in range(3):
            _waiting()

        with _limit(1):
            assert _place(pay_way=_cod()).status == OrderStatus.PENDING


class TestTheLimitIsTheStoresChoice:
    def test_zero_turns_it_off(self):
        for _ in range(4):
            _waiting()

        with _limit(0):
            assert _place().status == OrderStatus.PENDING

    def test_the_default_is_declared_for_every_store(self):
        from django.conf import settings

        [entry] = [
            e for e in settings.EXTRA_SETTINGS_DEFAULTS if e["name"] == SETTING
        ]

        assert entry["type"] == "int"
        assert entry["value"] > 0
        assert entry["description"]

    def test_the_declared_default_applies_when_the_store_chose_nothing(self):
        for _ in range(5):
            _waiting()

        with pytest.raises(InvalidOrderDataError):
            _place()


@pytest.mark.usefixtures("checkout_legal_documents")
class TestThroughTheEndpoint:
    """The shopper reads why: the limit has its own code and sentence on
    the payment method, not the generic ``invalid_order_data``."""

    @pytest.fixture(autouse=True)
    def _a_store_with_viva(self):
        from region.factories import RegionFactory
        from tests.integration.order.test_agent_order_unpaid_cart import (
            _Cart,
            _viva,
        )

        with patch(
            "pay_way.services.PayWayService.is_provider_configured",
            return_value=True,
        ):
            self.checkout = _Cart()
            self.region = RegionFactory(country=self.checkout.country)
            self.viva = _viva()
            yield

    def _post(self):
        from django.urls import reverse
        from rest_framework.test import APIClient

        address = self.checkout.address()
        return APIClient().post(
            reverse("order-list"),
            {
                "payWayId": self.viva.id,
                "termsAccepted": True,
                "firstName": address["first_name"],
                "lastName": address["last_name"],
                "email": address["email"],
                "street": address["street"],
                "streetNumber": address["street_number"],
                "city": address["city"],
                "zipcode": address["zipcode"],
                "countryId": address["country_id"],
                "regionId": self.region.alpha,
                "phone": address["phone"],
            },
            format="json",
            HTTP_X_CART_ID=str(self.checkout.cart.uuid),
            HTTP_X_LANGUAGE="en",
        )

    def test_the_refusal_has_its_own_code_and_sentence(self, documented):
        _waiting(email=self.checkout.address()["email"], pay_way=self.viva)
        before = Order.objects.count()

        with _limit(1):
            response = self._post()

        message = (
            "You already have 1 orders waiting for their payment. Pay for "
            "or cancel one before placing another."
        )
        assert response.status_code == 400, response.json()
        body = response.json()
        assert body["code"] == "unpaid_order_limit"
        assert body["fields"]["payWayId"] == [
            {"message": message, "code": "unpaid_order_limit"}
        ]
        documented(response, "/api/v1/order", "post")
        assert Order.objects.count() == before
