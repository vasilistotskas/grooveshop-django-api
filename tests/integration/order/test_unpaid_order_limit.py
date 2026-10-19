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
from order.exceptions import InvalidOrderDataError
from order.factories import OrderFactory
from order.models.order import Order
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from tests.utils.shipping import enable_rate
from user.factories import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

SETTING = "ORDER_MAX_UNPAID_ONLINE_ORDERS"


def _limit(value):
    return patch(
        "order.services.Setting.get",
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

        with _limit(2), pytest.raises(InvalidOrderDataError) as excinfo:
            _place()

        assert "already have 2 orders waiting" in str(excinfo.value)
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
            {"payment_status": PaymentStatus.FAILED},
        ],
    )
    def test_a_paid_canceled_or_failed_order_does_not(self, state):
        _waiting(**state)

        with _limit(1):
            assert _place().status == OrderStatus.PENDING

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
