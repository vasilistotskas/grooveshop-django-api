"""``POST /cart/quote`` and the total an order is created at (#635).

The quote is ``OrderQuoteService.quote``, the function order creation
runs under its locks. These tests pin the wire: a row that depends on a
choice the request did not make is null, every refusal has a code, and an
order created with ``expectedTotal`` exists only at that total.
``test_free_threshold_basis.py`` pins that the quote, the payment intent
and both creation paths agree on the amount.
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from django.core.cache import cache
from django.db import connection
from django.urls import reverse
from djmoney.money import Money
from knox.models import get_token_model
from measurement.measures import Weight
from rest_framework.test import APIClient

from cart.factories import CartFactory, CartItemFactory
from cart.models import Cart
from cart.throttles import (
    CartMutationAnonThrottle,
    CartQuoteAnonThrottle,
    CartQuoteThrottle,
)
from country.factories import CountryFactory
from giftcard.enum import GiftCardTransactionKind
from giftcard.factories import GiftCardFactory
from giftcard.models import GiftCardTransaction
from giftcard.throttles import GiftCardCodeThrottle
from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction
from loyalty.services import LoyaltyService
from order.checkout.quote import ExpectedTotal, OrderQuoteService
from order.exceptions import InvalidOrderDataError, OrderTotalChangedError
from order.models import OrderVatLine
from order.models.order import Order
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory, PayWayShippingExclusionFactory
from product.factories import ProductFactory
from promotion.enum import BenefitType, PromotionTrigger
from promotion.factories import PromotionCodeFactory, PromotionFactory
from promotion.models import CartPromotionCode, PromotionRedemption
from shipping.exceptions import ShippingParcelTooLargeError
from shipping.interfaces import get_provider
from shipping.models import ShippingProvider
from shipping_acs import config as acs_config
from shipping_acs.exceptions import AcsAPIError
from tests.utils.checkout import UNQUOTED, priced, quoted_body
from tests.utils.fake_stripe import fake_stripe
from tests.utils.legal import checkout_acceptance
from tests.utils.redis import private_cache
from tests.utils.shipping import enable_rate
from user.factories import UserAccountFactory
from vat.factories import VatFactory

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("checkout_legal_documents"),
]

PRICE = Decimal("20.00")
SHIPPING = Decimal("3.00")
FEE = Decimal("2.00")
THRESHOLD = Decimal("50.00")
TOTAL = PRICE + SHIPPING + FEE

QUOTE_PATH = "/api/v1/cart/quote"
ORDER_PATH = "/api/v1/order"


STORE_SETTINGS = {
    "PROMOTIONS_ENABLED": True,
    "LOYALTY_ENABLED": True,
    "LOYALTY_REDEMPTION_RATIO_EUR": 100.0,
    "GIFT_CARDS_ENABLED": True,
}


def _store_settings(**overrides):
    values = {**STORE_SETTINGS, **overrides}
    return patch(
        "extra_settings.models.Setting.get",
        side_effect=lambda key, default=None: values.get(key, default),
    )


@pytest.fixture(autouse=True)
def _features_on():
    with _store_settings():
        yield


def _product(price: Decimal = PRICE):
    return ProductFactory(
        stock=10,
        price=Money(price, "EUR"),
        discount_percent=Decimal(0),
        vat=VatFactory(value=0),
        active=True,
        num_images=0,
        num_reviews=0,
    )


def _checkout(price: Decimal = PRICE, *, user=True):
    """One line of ``price``, a 3.00 delivery and a 2.00 cash-on-delivery
    fee, both free from 50.00."""
    owner = UserAccountFactory(num_addresses=0) if user else None
    country = CountryFactory()
    enable_rate(country, price=SHIPPING, free_shipping_threshold=THRESHOLD)
    # ``CartFactory`` gets-or-creates on the user, so a second guest
    # cart would be the first one again.
    cart = CartFactory(user=owner) if owner else Cart.objects.create()
    cart.items.all().delete()
    product = _product(price)
    CartItemFactory(cart=cart, product=product, quantity=1)
    pay_way = PayWayFactory(
        settlement=PaySettlement.COURIER_CASH,
        active=True,
        cost=Money(FEE, "EUR"),
        free_threshold=Money(THRESHOLD, "EUR"),
    )
    client = APIClient()
    if owner is not None:
        client.force_authenticate(user=owner)
    return SimpleNamespace(
        user=owner,
        cart=cart,
        product=product,
        pay_way=pay_way,
        country=country,
        client=client,
    )


def _post_quote(checkout, client=None, **body):
    return (client or checkout.client).post(
        reverse("cart-quote"),
        body,
        format="json",
        HTTP_X_CART_ID=str(checkout.cart.uuid),
    )


def _choices(checkout) -> dict:
    return {
        "pay_way_id": checkout.pay_way.id,
        "shipping_kind": "home_delivery",
        "country_id": checkout.country.alpha_2,
        "email": "eleni@example.com",
    }


def _quote(checkout, **body) -> dict:
    response = _post_quote(checkout, **body)
    assert response.status_code == 200, response.content
    return response.json()


def _address(checkout) -> dict:
    return {
        "first_name": "Eleni",
        "last_name": "Georgiou",
        "email": "eleni@example.com",
        "street": "Akadimias",
        "street_number": "3",
        "city": "Athens",
        "zipcode": "10671",
        "country_id": checkout.country.alpha_2,
        "phone": "+306900000002",
        "shipping_kind": "home_delivery",
    }


def _post_order(checkout, *, expected_total=None, **extra):
    """Place the order, at ``expected_total`` or else at its own quote's."""
    body = {
        **_address(checkout),
        "pay_way_id": checkout.pay_way.id,
        "terms_accepted": True,
        **extra,
    }
    headers = {"HTTP_X_CART_ID": str(checkout.cart.uuid)}
    if expected_total is None:
        body = quoted_body(checkout.client, body, **headers)
    else:
        body["expected_total"] = expected_total
    return checkout.client.post(
        reverse("order-list"), body, format="json", **headers
    )


def _money(value) -> Decimal:
    return Decimal(str(value))


class TestPartialQuote:
    """A row is priced once its choice is made, and null until then."""

    def test_nothing_chosen(self, documented):
        checkout = _checkout()

        response = _post_quote(checkout)

        assert response.status_code == 200, response.content
        documented(response, QUOTE_PATH, "post")
        quote = response.json()
        assert _money(quote["itemsSubtotal"]) == PRICE
        assert _money(quote["discountTotal"]) == 0
        assert _money(quote["freeThresholdValue"]) == PRICE
        assert _money(quote["loyaltyDiscount"]) == 0
        assert quote["appliedPromotions"] == []
        assert quote["blockingCoupon"] is None
        assert quote["currency"] == "EUR"
        for row in (
            "shipping",
            "paymentMethodFee",
            "giftCardAmount",
            "grandTotal",
            "amountDue",
            "expectedTotal",
            "shippingOptions",
        ):
            assert quote[row] is None, row
        # Pay ways need no delivery to be priced: the fee is judged on
        # the products alone.
        offered = {row["id"]: row for row in quote["payWays"]}
        assert _money(offered[checkout.pay_way.id]["fee"]) == FEE
        assert offered[checkout.pay_way.id]["waived"] is False

    def test_a_country_lists_the_deliveries_at_their_price(self):
        checkout = _checkout()

        quote = _quote(checkout, country_id=checkout.country.alpha_2)

        assert quote["shipping"] is None
        assert quote["amountDue"] is None
        [option] = quote["shippingOptions"]
        assert option["kind"] == "home_delivery"
        assert _money(option["price"]) == SHIPPING
        assert option["waivedBy"] is None
        assert option["exceedsMaxWeight"] is False

    def test_a_delivery_without_a_pay_way(self):
        checkout = _checkout()

        quote = _quote(
            checkout,
            shipping_kind="home_delivery",
            country_id=checkout.country.alpha_2,
        )

        assert _money(quote["shipping"]["price"]) == SHIPPING
        assert quote["paymentMethodFee"] is None
        assert quote["grandTotal"] is None
        assert quote["amountDue"] is None
        assert quote["expectedTotal"] is None

    def test_a_pay_way_without_a_delivery(self):
        checkout = _checkout()

        quote = _quote(checkout, pay_way_id=checkout.pay_way.id)

        assert _money(quote["paymentMethodFee"]["amount"]) == FEE
        assert quote["shipping"] is None
        assert quote["amountDue"] is None

    def test_both_chosen_is_a_total(self, documented):
        checkout = _checkout()

        response = _post_quote(checkout, **_choices(checkout))

        assert response.status_code == 200, response.content
        documented(response, QUOTE_PATH, "post")
        quote = response.json()
        assert _money(quote["grandTotal"]) == TOTAL
        assert _money(quote["giftCardAmount"]) == 0
        assert _money(quote["amountDue"]) == TOTAL
        assert quote["expectedTotal"] == {"amount": 2500, "currency": "EUR"}


class TestQuoteRows:
    def test_a_threshold_waives_the_delivery_and_the_fee(self):
        checkout = _checkout(Decimal("60.00"))

        quote = _quote(checkout, **_choices(checkout))

        assert quote["shipping"] == {"price": 0, "waivedBy": "threshold"}
        assert quote["paymentMethodFee"] == {"amount": 0, "waived": True}
        assert _money(quote["amountDue"]) == Decimal("60.00")
        [option] = quote["shippingOptions"]
        assert option["waivedBy"] == "threshold"
        offered = {row["id"]: row for row in quote["payWays"]}
        assert offered[checkout.pay_way.id]["waived"] is True
        assert _money(offered[checkout.pay_way.id]["fee"]) == 0

    def test_a_promotion_waives_every_delivery(self):
        checkout = _checkout()
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FREE_SHIPPING,
        )

        quote = _quote(checkout, **_choices(checkout))

        assert quote["shipping"] == {"price": 0, "waivedBy": "promotion"}
        assert [row["waivedBy"] for row in quote["shippingOptions"]] == [
            "promotion"
        ]
        assert _money(quote["amountDue"]) == PRICE + FEE

    def test_a_discount_is_itemised(self):
        checkout = _checkout()
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal("5.00"),
        )

        quote = _quote(checkout, **_choices(checkout))

        assert _money(quote["discountTotal"]) == Decimal("5.00")
        [applied] = quote["appliedPromotions"]
        assert applied["promotionId"] == promotion.id
        assert applied["code"] is None
        assert _money(applied["amount"]) == Decimal("5.00")
        assert _money(quote["amountDue"]) == TOTAL - Decimal("5.00")

    def test_a_coupon_order_creation_refuses_is_reported(self):
        """Order creation refuses this cart (``invalid_coupon``); the
        quote says so instead of failing, and prices without the code."""
        checkout = _checkout()
        promotion = PromotionFactory(usage_limit_total=1)
        code = PromotionCodeFactory(promotion=promotion)
        PromotionRedemption.objects.create(
            promotion=promotion, code=code, amount=Money(Decimal(5), "EUR")
        )
        CartPromotionCode.objects.create(cart=checkout.cart, code=code)

        quote = _quote(checkout, **_choices(checkout))

        assert quote["blockingCoupon"]["code"] == code.code
        assert "usage_limit" in quote["blockingCoupon"]["reason"]
        assert _money(quote["discountTotal"]) == 0
        assert _money(quote["amountDue"]) == TOTAL


class TestQuoteRefusals:
    def test_no_cart_is_404_and_creates_none(self):
        checkout = _checkout(user=False)
        carts = Cart.objects.count()

        response = APIClient().post(
            reverse("cart-quote"),
            {},
            format="json",
            HTTP_X_CART_ID=str(uuid4()),
        )

        assert response.status_code == 404
        assert response.json()["code"] == "cart_not_found"
        assert Cart.objects.count() == carts
        assert checkout.cart.items.exists()

    def test_an_account_cart_is_not_reached_by_its_id(self):
        """An anonymous caller naming a signed-in shopper's cart gets 404,
        never that cart and never another one."""
        checkout = _checkout()
        carts = Cart.objects.count()

        response = _post_quote(checkout, client=APIClient())

        assert response.status_code == 404
        assert response.json()["code"] == "cart_not_found"
        assert Cart.objects.count() == carts

    def test_an_empty_cart(self):
        checkout = _checkout()
        checkout.cart.items.all().delete()

        response = _post_quote(checkout)

        assert response.status_code == 400
        assert response.json()["code"] == "cart_empty"

    def test_a_delivery_the_store_does_not_offer(self, documented):
        """A delivery that cannot be priced has its own code, so a client
        drops the selection instead of matching translated text."""
        checkout = _checkout()
        elsewhere = CountryFactory()

        response = _post_quote(
            checkout,
            **{**_choices(checkout), "country_id": elsewhere.alpha_2},
        )

        assert response.status_code == 400
        documented(response, QUOTE_PATH, "post")
        body = response.json()
        assert body["code"] == body["reason"] == "shipping_unavailable"
        assert body["detail"]
        assert "fieldErrors" not in body

    def test_a_cart_too_heavy_for_the_delivery(self):
        checkout = _checkout()
        enable_rate(checkout.country, price=SHIPPING, max_weight_grams=1)
        checkout.product.weight = Weight(kg=2)
        checkout.product.save()

        response = _post_quote(checkout, **_choices(checkout))

        assert response.status_code == 400
        assert response.json()["code"] == "shipping_weight_exceeded"

    def test_a_cart_too_large_for_the_delivery(self):
        checkout = _checkout()

        with patch.object(
            get_provider("flat_rate"),
            "assert_parcel_fits",
            side_effect=ShippingParcelTooLargeError(detail="too big"),
        ):
            response = _post_quote(checkout, **_choices(checkout))

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == body["reason"] == "shipping_parcel_too_large"
        # The carrier's own wording stays in the log.
        assert "too big" not in body["detail"]

    def test_the_payment_intent_keeps_its_own_answer(self):
        """Only the quote names the refusal: the payment intent and order
        creation answer ``invalid_order_data`` with the field, as before."""
        checkout = _checkout()
        stripe = PayWayFactory.create_online_payment(provider_code="stripe")
        elsewhere = CountryFactory()

        with fake_stripe():
            response = checkout.client.post(
                reverse("cart-create-payment-intent"),
                {
                    **_choices(checkout),
                    "pay_way_id": stripe.id,
                    "country_id": elsewhere.alpha_2,
                },
                format="json",
                HTTP_X_CART_ID=str(checkout.cart.uuid),
            )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "invalid_order_data"
        assert body["fieldErrors"]["shippingProviderCode"]

    def test_a_pay_way_that_is_switched_off(self):
        checkout = _checkout()
        checkout.pay_way.active = False
        checkout.pay_way.save(update_fields=["active"])

        response = _post_quote(checkout, **_choices(checkout))

        assert response.status_code == 400
        assert response.json()["code"] == "pay_way_unavailable"

    def test_a_pay_way_is_accepted_exactly_when_it_is_listed(self):
        """The selection and ``payWays`` go through one rule
        (``OrderQuoteService.offered_pay_ways``): a home delivery that
        names no carrier is judged by every carrier that could take it,
        and a provider the store holds no credentials for is not offered.
        Before, the selection applied neither, so the quote priced pay
        ways its own list left out."""
        checkout = _checkout()
        pay_ways = [
            checkout.pay_way,
            PayWayFactory.create_carrier_terminal_payment(),
            # No credentials in this store: not offered.
            PayWayFactory.create_online_payment(provider_code="stripe"),
            PayWayFactory.create_offline_payment(),
        ]
        listed = {
            row["id"]
            for row in _quote(checkout, **_choices(checkout))["payWays"]
        }

        accepted = {
            pay_way.id
            for pay_way in pay_ways
            if _post_quote(
                checkout, **{**_choices(checkout), "pay_way_id": pay_way.id}
            ).status_code
            == 200
        }

        assert accepted == listed & {pay_way.id for pay_way in pay_ways}
        assert checkout.pay_way.id in accepted
        assert pay_ways[2].id not in accepted

    def test_a_pay_way_that_does_not_exist(self):
        checkout = _checkout()

        response = _post_quote(
            checkout, **{**_choices(checkout), "pay_way_id": 999_999}
        )

        assert response.status_code == 404
        assert response.json()["code"] == "pay_way_not_found"

    def test_points_without_a_signed_in_customer(self):
        checkout = _checkout(user=False)

        response = _post_quote(
            checkout, **_choices(checkout), loyalty_points_to_redeem=100
        )

        assert response.status_code == 400
        assert response.json()["code"] == "loyalty_requires_authentication"

    def test_a_gift_card_that_cannot_be_used(self):
        checkout = _checkout()

        response = _post_quote(
            checkout, **_choices(checkout), gift_card_codes=["GC-NOPE-NOPE"]
        )

        assert response.status_code == 400
        assert response.json()["code"].startswith("gift_card")

    def test_a_pickup_point_needs_its_carrier(self):
        checkout = _checkout()

        response = _post_quote(
            checkout, **{**_choices(checkout), "shipping_kind": "pickup_point"}
        )

        assert response.status_code == 400
        assert response.json()["code"] == "shipping_provider_required"


def _cash(*, active: bool = True):
    return PayWayFactory(settlement=PaySettlement.COURIER_CASH, active=active)


def _own(checkout):
    return checkout.pay_way


def _switched_off(_checkout):
    return _cash(active=False)


def _without_credentials(_checkout):
    return PayWayFactory.create_online_payment(provider_code="stripe")


def _locker_terminal(_checkout):
    return PayWayFactory.create_carrier_terminal_payment()


def _excluded_for_home_delivery(_checkout, country=None):
    """Cash the store switched off for its flat-rate home delivery."""
    pay_way = _cash()
    PayWayShippingExclusionFactory(
        pay_way=pay_way,
        shipping_provider=ShippingProvider.objects.get(code="flat_rate"),
        shipping_kind="home_delivery",
        country=country,
    )
    return pay_way


def _excluded_in_another_country(checkout):
    return _excluded_for_home_delivery(checkout, country=CountryFactory())


def _refusal_code(response) -> str:
    return response.json()["code"]


class TestPayWaysAreJudgedOnce:
    """The quote, the payment intent and order creation accept and refuse
    the same pay ways: each asks ``OrderQuoteService.offered_pay_ways``.

    Order creation had a rule of its own, which judged a home delivery
    that names no carrier by no carrier at all: it created orders with a
    pay way the quote would not list and the intent would not charge.
    """

    @pytest.mark.parametrize(
        ("pay_way_of", "delivery", "offered"),
        [
            pytest.param(_own, {}, True, id="offered"),
            pytest.param(_switched_off, {}, False, id="switched-off"),
            pytest.param(
                _without_credentials, {}, False, id="provider-without-keys"
            ),
            pytest.param(
                _locker_terminal,
                {},
                False,
                id="locker-terminal-on-a-home-delivery",
            ),
            pytest.param(
                _excluded_for_home_delivery,
                {},
                False,
                id="excluded-by-the-carrier-that-would-deliver",
            ),
            pytest.param(
                _excluded_for_home_delivery,
                {"shipping_provider_code": "flat_rate"},
                False,
                id="excluded-for-the-named-carrier",
            ),
            pytest.param(
                _excluded_in_another_country,
                {},
                True,
                id="excluded-in-another-country",
            ),
        ],
    )
    def test_the_three_routes_agree(self, pay_way_of, delivery, offered):
        checkout = _checkout()
        pay_way = pay_way_of(checkout)
        choices = {**_choices(checkout), **delivery}
        del choices["pay_way_id"]

        listed = {row["id"] for row in _quote(checkout, **choices)["payWays"]}
        quote = _post_quote(checkout, **choices, pay_way_id=pay_way.id)
        intent = _post_intent(checkout, pay_way, **delivery)
        order = _post_order(
            checkout,
            **delivery,
            pay_way_id=pay_way.id,
            expected_total=(
                quote.json()["expectedTotal"]
                if quote.status_code == 200
                else UNQUOTED
            ),
        )

        assert (pay_way.id in listed) is offered
        if offered:
            assert quote.status_code == 200, quote.content
            # Past the pay-way rule and refused at the next gate: cash
            # is not paid with a card.
            assert intent.json()["code"] == "pay_way_not_intent_capable"
            assert order.status_code == 201, order.content
            return
        for response in (quote, intent, order):
            assert response.status_code == 400, response.content
            assert _refusal_code(response) == "pay_way_unavailable"

    @pytest.mark.parametrize(
        ("pay_way_of", "offered"),
        [
            pytest.param(_own, True, id="offered"),
            pytest.param(_locker_terminal, False, id="locker-terminal"),
            pytest.param(
                _excluded_for_home_delivery,
                False,
                id="excluded-by-the-carrier-that-would-deliver",
            ),
        ],
    )
    def test_an_order_without_a_kind_is_judged_as_a_home_delivery(
        self, pay_way_of, offered
    ):
        """``shippingKind`` is optional on order creation alone. An order
        without one is priced and assigned as a home delivery
        (``OrderQuoteService.delivery_kind``), so its pay way is judged
        as one: the answer is the quote's for a home delivery. Judged by
        no delivery at all, it took any active pay way."""
        checkout = _checkout()
        pay_way = pay_way_of(checkout)
        body = {
            **_address(checkout),
            "pay_way_id": pay_way.id,
            "terms_accepted": True,
        }
        del body["shipping_kind"]

        quote = _post_quote(
            checkout, **{**_choices(checkout), "pay_way_id": pay_way.id}
        )
        order = checkout.client.post(
            reverse("order-list"),
            {
                **body,
                "expected_total": (
                    quote.json()["expectedTotal"]
                    if quote.status_code == 200
                    else UNQUOTED
                ),
            },
            format="json",
            HTTP_X_CART_ID=str(checkout.cart.uuid),
        )

        if offered:
            assert quote.status_code == 200, quote.content
            assert order.status_code == 201, order.content
            created = Order.objects.get(id=order.json()["id"])
            assert created.shipping_kind == "home_delivery"
            return
        for response in (quote, order):
            assert response.status_code == 400, response.content
            assert _refusal_code(response) == "pay_way_unavailable"


def _cart_token(checkout) -> str:
    _auth, raw = get_token_model().objects.create(checkout.user)
    shopper = APIClient()
    shopper.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return shopper.post(reverse("cart-agent-token")).data["token"]


class TestCartToken:
    def test_a_token_quotes_the_owners_cart(self):
        """The shopping assistant asks anonymously, with the signed-in
        shopper's cart token: the quote is that cart's, at its owner's
        prices."""
        checkout = _checkout()
        assistant = APIClient()
        assistant.credentials(HTTP_X_CART_TOKEN=_cart_token(checkout))

        response = assistant.post(
            reverse("cart-quote"), _choices(checkout), format="json"
        )

        assert response.status_code == 200, response.content
        assert _money(response.json()["amountDue"]) == TOTAL

    @pytest.mark.parametrize("points", [100, 5000])
    def test_a_token_cannot_price_the_owners_points(self, points):
        """The token names the owner without being the owner. Pricing the
        owner's points would tell its holder the balance (the refusal of
        too many points says how many there are), so points on a token
        request are refused before anything is priced."""
        checkout = _checkout()
        PointsTransaction.objects.create(
            user=checkout.user,
            points=137,
            transaction_type=TransactionType.EARN,
        )
        assistant = APIClient()
        assistant.credentials(HTTP_X_CART_TOKEN=_cart_token(checkout))

        response = assistant.post(
            reverse("cart-quote"),
            {**_choices(checkout), "loyalty_points_to_redeem": points},
            format="json",
        )

        assert response.status_code == 400
        assert response.json()["code"] == "loyalty_requires_authentication"
        assert "137" not in response.content.decode()

    def test_another_account_holding_the_token_cannot_either(self):
        checkout = _checkout()
        PointsTransaction.objects.create(
            user=checkout.user,
            points=137,
            transaction_type=TransactionType.EARN,
        )
        someone_else = APIClient()
        someone_else.force_authenticate(
            user=UserAccountFactory(num_addresses=0)
        )
        someone_else.credentials(HTTP_X_CART_TOKEN=_cart_token(checkout))

        response = someone_else.post(
            reverse("cart-quote"),
            {**_choices(checkout), "loyalty_points_to_redeem": 5000},
            format="json",
        )

        assert response.status_code == 400
        assert response.json()["code"] == "loyalty_requires_authentication"
        assert "137" not in response.content.decode()

    def test_the_owner_still_reads_why_their_points_are_refused(self):
        checkout = _checkout()
        PointsTransaction.objects.create(
            user=checkout.user,
            points=137,
            transaction_type=TransactionType.EARN,
        )

        response = _post_quote(
            checkout, **_choices(checkout), loyalty_points_to_redeem=5000
        )

        assert response.status_code == 400
        assert response.json()["code"] == "loyalty_redemption_invalid"
        assert "137" in response.json()["detail"]


def _private_budget(monkeypatch, throttle, rate: str) -> None:
    """Give one throttle a rate (the suite runs with none) and a counter
    of its own, so no other xdist worker shares its history."""
    monkeypatch.setattr(throttle, "rate", rate, raising=False)
    monkeypatch.setattr(throttle, "cache", private_cache(), raising=False)


class TestQuoteBudget:
    """The quote is polled: it has a budget of its own."""

    def test_quotes_do_not_spend_the_cart_write_budget(self, monkeypatch):
        _private_budget(monkeypatch, CartMutationAnonThrottle, "2/minute")
        checkout = _checkout(user=False)

        statuses = [_post_quote(checkout).status_code for _ in range(5)]

        assert statuses == [200] * 5

    def test_the_quote_budget_binds(self, monkeypatch):
        _private_budget(monkeypatch, CartQuoteAnonThrottle, "3/minute")
        checkout = _checkout(user=False)

        statuses = [_post_quote(checkout).status_code for _ in range(4)]

        assert statuses == [200, 200, 200, 429]

    def test_the_gateway_is_budgeted_per_cart(self, monkeypatch, settings):
        """Guests are budgeted by ``CartQuoteAnonThrottle`` alone. The
        signed-in budget does not count them: DRF's ``UserRateThrottle``
        would, by address, and every buyer of a platform would share it."""
        settings.AGENT_GATEWAY_INTERNAL_SECRET = "gateway-secret"
        _private_budget(monkeypatch, CartQuoteAnonThrottle, "2/minute")
        _private_budget(monkeypatch, CartQuoteThrottle, "2/minute")
        polled, other = _checkout(user=False), _checkout(user=False)
        gateway = APIClient()
        gateway.credentials(HTTP_X_INTERNAL_GATEWAY="gateway-secret")

        statuses = [
            _post_quote(polled, client=gateway).status_code for _ in range(3)
        ]

        assert statuses == [200, 200, 429]
        assert _post_quote(other, client=gateway).status_code == 200


def _post_intent(checkout, pay_way, **extra):
    return checkout.client.post(
        reverse("cart-create-payment-intent"),
        {**_choices(checkout), "pay_way_id": pay_way.id, **extra},
        format="json",
        HTTP_X_CART_ID=str(checkout.cart.uuid),
    )


def _post_encoded(checkout, url_name: str, data: dict, encoding: str):
    """POST ``data`` as a multipart or a url-encoded form body."""
    headers = {"HTTP_X_CART_ID": str(checkout.cart.uuid)}
    if encoding == "multipart":
        return checkout.client.post(
            reverse(url_name), data, format="multipart", **headers
        )
    return checkout.client.post(
        reverse(url_name),
        urlencode(data, doseq=True),
        content_type="application/x-www-form-urlencoded",
        **headers,
    )


def _guess(number: int) -> dict:
    return {"gift_card_codes": [f"GC-GUESS-{number:04d}"]}


class TestGiftCardGuessesAreBudgeted:
    """Every route that prices a checkout says what a gift card settles
    (or that the code is no card), so each could be used to guess codes.
    What is limited is guessing, not use: only a code new to the cart
    counts, six a minute, and sending a tried code again is free."""

    LIMIT = 6

    @pytest.fixture(autouse=True)
    def _six_a_minute(self, monkeypatch):
        _private_budget(
            monkeypatch, GiftCardCodeThrottle, f"{self.LIMIT}/minute"
        )

    def _spend(self, post) -> list[int]:
        """Try LIMIT new codes, then one more."""
        return [post(_guess(n)).status_code for n in range(self.LIMIT + 1)]

    def test_requoting_one_card_is_never_refused(self):
        checkout = _checkout()
        card = GiftCardFactory(initial_value=Money(Decimal("10.00"), "EUR"))
        with_card = {**_choices(checkout), "gift_card_codes": [card.code]}

        statuses = {
            _post_quote(checkout, **with_card).status_code for _ in range(20)
        }

        assert statuses == {200}

    def test_the_seventh_new_code_is_refused_on_the_quote(self):
        checkout = _checkout()

        statuses = self._spend(
            lambda codes: _post_quote(checkout, **_choices(checkout), **codes)
        )

        assert statuses == [400] * self.LIMIT + [429]
        # A code already tried is not a new guess, and a quote without
        # codes was never counted.
        again = _post_quote(checkout, **_choices(checkout), **_guess(0))
        assert again.status_code == 400
        assert _post_quote(checkout, **_choices(checkout)).status_code == 200

    def test_one_card_typed_two_ways_is_one_code(self):
        checkout = _checkout()
        spellings = ["gc-guess-0000", " GC-GUESS-0000 ", "Gc-Guess-0000"] * 3

        statuses = {
            _post_quote(
                checkout, **_choices(checkout), gift_card_codes=[code]
            ).status_code
            for code in spellings
        }

        assert statuses == {400}

    def test_every_new_code_of_a_request_counts(self):
        checkout = _checkout()

        def three(start: int):
            codes = [f"GC-GUESS-{n:04d}" for n in range(start, start + 3)]
            return _post_quote(
                checkout, **_choices(checkout), gift_card_codes=codes
            ).status_code

        assert [three(0), three(3)] == [400, 400]
        assert three(6) == 429
        # The refused codes were never tried: one of them is still new.
        assert (
            _post_quote(checkout, **_choices(checkout), **_guess(6)).status_code
            == 429
        )

    def test_on_the_payment_intent(self):
        checkout = _checkout()
        stripe = PayWayFactory.create_online_payment(provider_code="stripe")

        with fake_stripe():
            statuses = self._spend(
                lambda codes: _post_intent(checkout, stripe, **codes)
            )
            again = _post_intent(checkout, stripe, **_guess(0))
            without_codes = _post_intent(checkout, stripe)

        assert statuses == [400] * self.LIMIT + [429]
        assert again.status_code == 400
        assert without_codes.status_code == 200, without_codes.content

    def test_on_order_creation(self):
        checkout = _checkout()

        statuses = self._spend(
            lambda codes: _post_order(
                checkout, **codes, expected_total=UNQUOTED
            )
        )

        assert statuses == [400] * self.LIMIT + [429]
        assert (
            _post_order(
                checkout, **_guess(0), expected_total=UNQUOTED
            ).status_code
            == 400
        )
        assert not Order.objects.exists()
        assert _post_order(checkout).status_code == 201

    @pytest.mark.parametrize("encoding", ["multipart", "form"])
    @pytest.mark.parametrize("route", ["quote", "intent", "order"])
    def test_a_form_body_is_counted_like_json(self, route, encoding):
        """The serializers read every code of a form or multipart body
        (repeated keys), so the budget must read them the same way: it
        takes them from the request serializer's own field."""
        checkout = _checkout()
        stripe = PayWayFactory.create_online_payment(provider_code="stripe")
        url_name, body = {
            "quote": ("cart-quote", _choices(checkout)),
            "intent": (
                "cart-create-payment-intent",
                {**_choices(checkout), "pay_way_id": stripe.id},
            ),
            "order": (
                "order-list",
                {
                    **_address(checkout),
                    "pay_way_id": checkout.pay_way.id,
                    "terms_accepted": "true",
                    # A nested field in a form body, by its dotted keys.
                    **{
                        f"expected_total.{key}": value
                        for key, value in UNQUOTED.items()
                    },
                },
            ),
        }[route]

        with fake_stripe():
            statuses = self._spend(
                lambda codes: _post_encoded(
                    checkout, url_name, {**body, **codes}, encoding
                )
            )

        assert statuses == [400] * self.LIMIT + [429]
        assert not Order.objects.exists()

    @pytest.mark.parametrize("encoding", ["multipart", "form"])
    def test_every_code_of_a_form_body_counts(self, encoding):
        """Ten guesses under one repeated key are ten codes: more than
        the serializer takes (refused, nothing priced, nothing counted),
        and three at a time they spend the budget in two requests."""
        checkout = _checkout()

        def guess(numbers) -> int:
            codes = [f"GC-GUESS-{n:04d}" for n in numbers]
            return _post_encoded(
                checkout,
                "cart-quote",
                {**_choices(checkout), "gift_card_codes": codes},
                encoding,
            ).status_code

        assert guess(range(100, 110)) == 400
        assert [guess(range(3)), guess(range(3, 6))] == [400, 400]
        assert guess(range(6, 9)) == 429

    def test_the_routes_share_one_budget_and_one_record(self):
        """A code tried on the quote is not new to the order, and guesses
        on one route are not free on another."""
        checkout = _checkout()

        def quote(codes):
            return _post_quote(checkout, **_choices(checkout), **codes)

        assert [quote(_guess(n)).status_code for n in range(3)] == [400] * 3
        assert (
            _post_order(
                checkout, **_guess(0), expected_total=UNQUOTED
            ).status_code
            == 400
        )
        assert [
            _post_order(
                checkout, **_guess(n), expected_total=UNQUOTED
            ).status_code
            for n in (3, 4, 5, 6)
        ] == [400, 400, 400, 429]

    def test_two_gateway_carts_do_not_share_a_budget(self, settings):
        """Every buyer of a platform arrives from the platform's address:
        the gateway's requests are counted on their cart."""
        settings.AGENT_GATEWAY_INTERNAL_SECRET = "gateway-secret"
        one, other = _checkout(user=False), _checkout(user=False)
        gateway = APIClient()
        gateway.credentials(HTTP_X_INTERNAL_GATEWAY="gateway-secret")

        statuses = self._spend(
            lambda codes: _post_quote(
                one, client=gateway, **_choices(one), **codes
            )
        )

        assert statuses == [400] * self.LIMIT + [429]
        assert (
            _post_quote(
                other, client=gateway, **_choices(other), **_guess(99)
            ).status_code
            == 400
        )

    def test_a_storefront_callers_carts_share_one(self):
        """Without the gateway's secret the caller pays, whichever cart
        the guess is made on: a new cart is not a new budget."""
        one, other = _checkout(user=False), _checkout(user=False)
        visitor = APIClient()

        def guess(cart, number: int) -> int:
            return _post_quote(
                cart, client=visitor, **_choices(cart), **_guess(number)
            ).status_code

        assert [guess(one, n) for n in range(3)] == [400] * 3
        assert [guess(other, n) for n in range(3, 6)] == [400] * 3
        assert guess(other, 6) == 429
        assert guess(one, 7) == 429

    def test_the_record_holds_no_code(self):
        checkout = _checkout()

        _post_quote(checkout, **_choices(checkout), **_guess(0))

        [digest] = GiftCardCodeThrottle.cache.get(
            f"gift_card_codes_seen_user:{checkout.user.pk}"
        )
        assert "GUESS" not in digest
        assert len(digest) == 64


class TestPointsWorthLessThanACent:
    """4 points at 1000 a euro are 0.004: a discount of 0.00. Redeeming
    them would spend the points for nothing, so every path refuses and
    says how many points make a cent."""

    @pytest.fixture(autouse=True)
    def _a_thousand_points_a_euro(self):
        with _store_settings(LOYALTY_REDEMPTION_RATIO_EUR=1000.0):
            yield

    def _member(self):
        checkout = _checkout()
        PointsTransaction.objects.create(
            user=checkout.user,
            points=100,
            transaction_type=TransactionType.EARN,
        )
        return checkout

    def test_the_quote_refuses(self):
        checkout = self._member()

        response = _post_quote(
            checkout, **_choices(checkout), loyalty_points_to_redeem=4
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == body["reason"] == "loyalty_redemption_too_small"
        assert "5" in body["detail"]

    def test_five_points_are_a_cent(self):
        checkout = self._member()

        quote = _quote(
            checkout, **_choices(checkout), loyalty_points_to_redeem=5
        )

        assert _money(quote["loyaltyDiscount"]) == Decimal("0.01")

    def test_the_payment_intent_refuses(self):
        checkout = self._member()
        stripe = PayWayFactory.create_online_payment(provider_code="stripe")

        with fake_stripe():
            response = _post_intent(
                checkout, stripe, loyalty_points_to_redeem=4
            )

        assert response.status_code == 400
        assert response.json()["code"] == "loyalty_redemption_too_small"

    def test_order_creation_refuses_and_spends_nothing(self):
        checkout = self._member()

        response = _post_order(
            checkout, loyalty_points_to_redeem=4, expected_total=UNQUOTED
        )

        assert response.status_code == 400
        body = response.json()
        assert body["error"]["type"] == "invalid_order_data"
        assert "5" in body["fieldErrors"]["loyaltyPointsToRedeem"][0]
        assert not Order.objects.exists()
        assert LoyaltyService.get_user_balance(checkout.user) == 100
        checkout.product.refresh_from_db()
        assert checkout.product.stock == 10


@contextmanager
def _acs_prices_live(answer):
    """ACS as a live-priced home-delivery carrier whose
    ``ACS_Price_Calculation`` gives ``answer`` (or raises it). Yields the
    savepoint depth of every call: 0 extra means no transaction of the
    code under test was open, so no row lock was held."""
    depths: list[int] = []
    outside = len(connection.savepoint_ids)

    def price_calculation(_payload):
        depths.append(len(connection.savepoint_ids) - outside)
        if isinstance(answer, Exception):
            raise answer
        return answer

    with (
        _store_settings(ACS_DYNAMIC_PRICING_ENABLED=True),
        patch.object(acs_config, "is_configured", return_value=True),
        patch.object(acs_config, "station_origin", return_value="ΑΚ"),
        patch("shipping_acs.client.AcsClient") as client,
    ):
        client.return_value.price_calculation.side_effect = price_calculation
        cache.clear()
        yield depths


class TestLivePrice:
    """ACS prices live, over HTTP. The answer is cached per country,
    region and weight bracket (``AcsCarrier._fetch_live_quote``)."""

    LIVE = {"Total_Ammount": 5.21}

    def _checkout(self):
        checkout = _checkout()
        enable_rate(
            checkout.country,
            provider_code="acs",
            price=SHIPPING,
            free_shipping_threshold=THRESHOLD,
        )
        return checkout

    def _creation(self, checkout) -> dict:
        return {
            "legal_acceptance": checkout_acceptance(),
            "cart": checkout.cart,
            "shipping_address": {
                **_address(checkout),
                "shipping_provider_code": "acs",
            },
            "pay_way": checkout.pay_way,
            "user": checkout.user,
        }

    def _order(self, checkout, *, expected_total=None):
        """Create the order at ``expected_total``, or else at its quote's."""
        creation = self._creation(checkout)
        if expected_total is None:
            creation = priced(**creation)
        else:
            creation["expected_total"] = expected_total
        return OrderService.create_order_from_cart_offline(**creation)

    def test_a_polled_quote_asks_the_carrier_once(self):
        checkout = self._checkout()
        acs = {**_choices(checkout), "shipping_provider_code": "acs"}

        with _acs_prices_live(self.LIVE) as calls:
            first = _quote(checkout, **acs)
            second = _quote(checkout, **acs)

        assert _money(first["shipping"]["price"]) == Decimal("5.21")
        assert second["shipping"] == first["shipping"]
        assert second["expectedTotal"] == first["expectedTotal"]
        assert len(calls) == 1

    def test_order_creation_asks_the_carrier_before_its_locks(self):
        """A carrier call can take seconds. Order creation makes it with
        nothing locked, and prices from the cache once it holds the cart,
        the promotions, the customer and the gift cards."""
        checkout = self._checkout()
        with _acs_prices_live(self.LIVE):
            shown = priced(**self._creation(checkout))["expected_total"]

        # A fresh cache: the price the order is created at is asked for
        # by order creation itself.
        with _acs_prices_live(self.LIVE) as calls:
            order = self._order(checkout, expected_total=shown)

        assert order.shipping_price.amount == Decimal("5.21")
        assert calls == [0]

    def test_a_price_not_resolved_for_this_parcel_refuses_the_order(self):
        """The locked half finds no price for what it weighs (the cart
        changed after the price was resolved, or the entry expired). It
        does not ask the carrier: the creation rolls back, the cart is
        priced again with nothing locked, and the order is refused as a
        changed total with that quote."""
        checkout = self._checkout()
        # The buyer was shown the live price; its cache entry is gone by
        # the time the order is placed (``_acs_prices_live`` clears it).
        with _acs_prices_live(self.LIVE):
            shown = priced(**self._creation(checkout))["expected_total"]

        with (
            _acs_prices_live(self.LIVE) as calls,
            patch.object(OrderQuoteService, "resolve_live_shipping"),
            pytest.raises(OrderTotalChangedError) as refused,
        ):
            self._order(checkout, expected_total=shown)

        assert refused.value.quote.charges().shipping.price.amount == Decimal(
            "5.21"
        )
        assert calls == [0]
        assert not Order.objects.exists()
        checkout.product.refresh_from_db()
        assert checkout.product.stock == 10

        with _acs_prices_live(self.LIVE) as calls:
            order = self._order(
                checkout, expected_total=refused.value.quote.expected_total
            )

        assert order.shipping_price.amount == Decimal("5.21")
        assert calls == [0]

    def test_a_carrier_that_gave_no_price_is_remembered(self):
        """ACS down: the stored rate is charged, as before. The failure is
        remembered for a minute, so the locked half knows the answer was
        "no live price" and neither it nor the next quote asks again."""
        checkout = self._checkout()
        acs = {**_choices(checkout), "shipping_provider_code": "acs"}
        down = AcsAPIError(alias="ACS_Price_Calculation", error_message="x")

        with _acs_prices_live(down) as calls:
            quote = _quote(checkout, **acs)
            order = self._order(
                checkout, expected_total=ExpectedTotal(**quote["expectedTotal"])
            )

        assert _money(quote["shipping"]["price"]) == SHIPPING
        assert order.shipping_price.amount == SHIPPING
        assert calls == [0]


class TestExpectedTotal:
    """An order created with ``expectedTotal`` exists only at that total."""

    def test_the_quoted_total_creates_the_order(self):
        checkout = _checkout()
        quote = _quote(checkout, **_choices(checkout))

        response = _post_order(checkout, expected_total=quote["expectedTotal"])

        assert response.status_code == 201, response.content
        order = Order.objects.get()
        assert ExpectedTotal.of(order.amount_due) == ExpectedTotal(2500, "EUR")

    def _post_without_total(self, checkout, **extra):
        return checkout.client.post(
            reverse("order-list"),
            {
                **_address(checkout),
                "pay_way_id": checkout.pay_way.id,
                "terms_accepted": True,
                **extra,
            },
            format="json",
            HTTP_X_CART_ID=str(checkout.cart.uuid),
        )

    def _assert_required(self, response) -> None:
        assert response.status_code == 400, response.content
        [error] = response.json()["fields"]["expectedTotal"]
        assert error["code"] == "required"
        assert not Order.objects.exists()

    def test_an_order_first_order_without_one_is_refused(self, documented):
        checkout = _checkout()

        response = self._post_without_total(checkout)

        self._assert_required(response)
        documented(response, "/api/v1/order", "post")
        checkout.product.refresh_from_db()
        assert checkout.product.stock == 10
        assert checkout.cart.items.count() == 1

    def test_a_payment_first_order_without_one_is_refused(self):
        checkout = _checkout()
        stripe = PayWayFactory(
            provider_code="stripe",
            settlement=PaySettlement.ONLINE,
            active=True,
            cost=Money(FEE, "EUR"),
            free_threshold=Money(THRESHOLD, "EUR"),
        )
        with fake_stripe():
            intent = _post_intent(checkout, stripe)
            assert intent.status_code == 200, intent.content

            response = self._post_without_total(
                checkout,
                pay_way_id=stripe.id,
                payment_intent_id=intent.data["payment_intent_id"],
            )

        self._assert_required(response)

    def test_another_total_is_409_with_the_quote(self, documented):
        checkout = _checkout()

        response = _post_order(
            checkout, expected_total={"amount": 2499, "currency": "EUR"}
        )

        assert response.status_code == 409, response.content
        documented(response, ORDER_PATH, "post")
        body = response.json()
        assert body["code"] == "total_changed"
        # Not ``total_changed`` until the storefront in production lists
        # the value (``order.enum.create_error.TOTAL_CHANGED_CODE``).
        assert body["error"]["type"] == "invalid_order_data"
        quote = body["error"]["quote"]
        assert quote["expectedTotal"] == {"amount": 2500, "currency": "EUR"}
        assert _money(quote["amountDue"]) == TOTAL
        assert _money(quote["shipping"]["price"]) == SHIPPING
        # Nothing was created or taken.
        assert not Order.objects.exists()
        checkout.product.refresh_from_db()
        assert checkout.product.stock == 10
        assert checkout.cart.items.count() == 1

    def test_a_price_changed_after_the_quote(self):
        checkout = _checkout()
        quote = _quote(checkout, **_choices(checkout))
        checkout.product.price = Money(Decimal("21.00"), "EUR")
        checkout.product.save()

        response = _post_order(checkout, expected_total=quote["expectedTotal"])

        assert response.status_code == 409, response.content
        fresh = response.json()["error"]["quote"]
        assert fresh["expectedTotal"] == {"amount": 2600, "currency": "EUR"}
        assert not Order.objects.exists()

        again = _post_order(checkout, expected_total=fresh["expectedTotal"])

        assert again.status_code == 201, again.content

    def test_another_currency_is_another_total(self):
        checkout = _checkout()

        response = _post_order(
            checkout, expected_total={"amount": 2500, "currency": "USD"}
        )

        assert response.status_code == 409, response.content
        assert not Order.objects.exists()

    def test_a_malformed_total_is_a_validation_error(self):
        checkout = _checkout()

        response = _post_order(
            checkout, expected_total={"amount": -1, "currency": "EUR"}
        )

        assert response.status_code == 400
        assert "expectedTotal" in response.json()
        assert not Order.objects.exists()

    def test_the_payment_first_path_compares_it_too(self):
        checkout = _checkout()
        stripe = PayWayFactory(
            provider_code="stripe",
            settlement=PaySettlement.ONLINE,
            active=True,
            cost=Money(FEE, "EUR"),
            free_threshold=Money(THRESHOLD, "EUR"),
        )
        with fake_stripe():
            intent = checkout.client.post(
                reverse("cart-create-payment-intent"),
                {
                    "pay_way_id": stripe.id,
                    "shipping_kind": "home_delivery",
                    "country_id": checkout.country.alpha_2,
                },
                format="json",
                HTTP_X_CART_ID=str(checkout.cart.uuid),
            )
            assert intent.status_code == 200, intent.content

            def create(expected_total):
                return OrderService.create_order_from_cart(
                    legal_acceptance=checkout_acceptance(),
                    cart=checkout.cart,
                    shipping_address=_address(checkout),
                    payment_intent_id=intent.data["payment_intent_id"],
                    pay_way=stripe,
                    user=checkout.user,
                    expected_total=expected_total,
                )

            with pytest.raises(OrderTotalChangedError) as refused:
                create(ExpectedTotal(2499, "EUR"))
            assert refused.value.quote.expected_total == ExpectedTotal(
                2500, "EUR"
            )
            assert not Order.objects.exists()

            order = create(ExpectedTotal(2500, "EUR"))

        assert ExpectedTotal.of(order.amount_due) == ExpectedTotal(2500, "EUR")


class TestDeductions:
    """Loyalty points are a discount, gift cards are payment: both are
    rows of the quote, and neither is taken by an order that is refused."""

    def _member(self, points: int):
        checkout = _checkout()
        PointsTransaction.objects.create(
            user=checkout.user,
            points=points,
            transaction_type=TransactionType.EARN,
        )
        return checkout

    def test_points_and_gift_cards_are_rows(self, documented):
        checkout = self._member(500)
        card = GiftCardFactory(initial_value=Money(Decimal("10.00"), "EUR"))

        response = _post_quote(
            checkout,
            **_choices(checkout),
            loyalty_points_to_redeem=500,
            gift_card_codes=[card.code],
        )

        assert response.status_code == 200, response.content
        documented(response, QUOTE_PATH, "post")
        quote = response.json()
        assert _money(quote["grandTotal"]) == TOTAL
        assert _money(quote["loyaltyDiscount"]) == Decimal("5.00")
        assert _money(quote["giftCardAmount"]) == Decimal("10.00")
        assert _money(quote["amountDue"]) == Decimal("10.00")
        assert quote["expectedTotal"] == {"amount": 1000, "currency": "EUR"}
        # A quote redeems nothing.
        assert LoyaltyService.get_user_balance(checkout.user) == 500
        assert card.balance.amount == Decimal("10.00")

    def test_a_refused_total_takes_no_points_and_no_card_balance(self):
        checkout = self._member(500)
        card = GiftCardFactory(initial_value=Money(Decimal("10.00"), "EUR"))
        deductions = {
            "loyalty_points_to_redeem": 500,
            "gift_card_codes": [card.code],
        }

        response = _post_order(
            checkout,
            **deductions,
            expected_total={"amount": 2500, "currency": "EUR"},
        )

        assert response.status_code == 409, response.content
        fresh = response.json()["error"]["quote"]
        assert _money(fresh["loyaltyDiscount"]) == Decimal("5.00")
        assert _money(fresh["giftCardAmount"]) == Decimal("10.00")
        assert fresh["expectedTotal"] == {"amount": 1000, "currency": "EUR"}
        assert not Order.objects.exists()
        assert LoyaltyService.get_user_balance(checkout.user) == 500
        assert card.balance.amount == Decimal("10.00")
        assert not GiftCardTransaction.objects.filter(
            kind=GiftCardTransactionKind.REDEEM
        ).exists()
        checkout.product.refresh_from_db()
        assert checkout.product.stock == 10
        assert checkout.cart.items.count() == 1

        again = _post_order(
            checkout, **deductions, expected_total=fresh["expectedTotal"]
        )

        assert again.status_code == 201, again.content
        order = Order.objects.get()
        assert order.amount_due.amount == Decimal("10.00")
        assert LoyaltyService.get_user_balance(checkout.user) == 0
        assert card.balance.amount == 0

    def test_points_pay_for_goods_never_for_shipping_or_the_fee(self):
        """20.00 of goods less a 15.00 promotion leave 5.00 to pay for
        them. Points may take those 5.00 and no more: before, the cap was
        the goods before the promotion, and 10.00 of points also paid the
        delivery and the fee."""
        checkout = self._member(1000)
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal("15.00"),
        )

        too_many = _post_quote(
            checkout, **_choices(checkout), loyalty_points_to_redeem=1000
        )
        order_too_many = _post_order(
            checkout, loyalty_points_to_redeem=1000, expected_total=UNQUOTED
        )
        quote = _quote(
            checkout, **_choices(checkout), loyalty_points_to_redeem=500
        )

        assert too_many.status_code == 400
        assert too_many.json()["code"] == "loyalty_redemption_invalid"
        assert order_too_many.status_code == 400
        assert not Order.objects.exists()
        assert LoyaltyService.get_user_balance(checkout.user) == 1000
        assert _money(quote["loyaltyDiscount"]) == Decimal("5.00")
        assert _money(quote["amountDue"]) == SHIPPING + FEE

        response = _post_order(
            checkout,
            loyalty_points_to_redeem=500,
            expected_total=quote["expectedTotal"],
        )

        assert response.status_code == 201, response.content
        assert Order.objects.get().amount_due.amount == SHIPPING + FEE

    def test_points_worth_half_a_cent_are_rounded_once(self):
        """103 points at 200 a euro are 0.515: the quote, the order and
        the per-line snapshots the receipt reads must hold one figure.
        Unrounded, the order kept 0.52 and its lines 0.51, and creation
        failed when the VAT lines were written."""
        checkout = self._member(103)

        with _store_settings(LOYALTY_REDEMPTION_RATIO_EUR=200.0):
            quote = _quote(
                checkout, **_choices(checkout), loyalty_points_to_redeem=103
            )
            response = _post_order(
                checkout,
                loyalty_points_to_redeem=103,
                expected_total=quote["expectedTotal"],
            )

        assert _money(quote["loyaltyDiscount"]) == Decimal("0.52")
        assert quote["expectedTotal"] == {"amount": 2448, "currency": "EUR"}
        assert response.status_code == 201, response.content
        order = Order.objects.get()
        assert order.loyalty_discount.amount == Decimal("0.52")
        assert order.amount_due.amount == Decimal("24.48")
        assert sum(
            item.discount_allocation.amount for item in order.items.all()
        ) == Decimal("0.52")
        assert OrderVatLine.objects.filter(order=order).exists()


def test_an_order_that_does_not_add_up_to_its_quote_is_not_created():
    """The order is built from the quote's rows, so its own sum is the
    quote's total. A charge that reached one and not the other fails the
    creation instead of producing an order no payment matches."""
    checkout = _checkout()

    with (
        patch.object(
            Order,
            "calculate_order_total_amount",
            return_value=Money(Decimal("1.00"), "EUR"),
        ),
        pytest.raises(InvalidOrderDataError),
    ):
        OrderService.create_order_from_cart_offline(
            **priced(
                legal_acceptance=checkout_acceptance(),
                cart=checkout.cart,
                shipping_address=_address(checkout),
                pay_way=checkout.pay_way,
                user=checkout.user,
            )
        )

    assert not Order.objects.exists()
    checkout.product.refresh_from_db()
    assert checkout.product.stock == 10
