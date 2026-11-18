"""``POST /cart/quote`` and the total an order is created at (#635).

The quote is ``OrderQuoteService.quote``, the function order creation
runs under its locks. These tests pin the wire: a row that depends on a
choice the request did not make is null, every refusal has a code, and an
order created with ``expectedTotal`` exists only at that total.
``test_free_threshold_basis.py`` pins that the quote, the payment intent
and both creation paths agree on the amount.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.cache import cache
from django.core.cache.backends.locmem import LocMemCache
from django.urls import reverse
from djmoney.money import Money
from knox.models import get_token_model
from measurement.measures import Weight
from rest_framework.test import APIClient

from cart.factories import CartFactory, CartItemFactory
from cart.models import Cart
from core.api.throttling import (
    CartMutationAnonThrottle,
    CartQuoteAnonThrottle,
    GiftCardCheckThrottle,
)
from country.factories import CountryFactory
from giftcard.enum import GiftCardTransactionKind
from giftcard.factories import GiftCardFactory
from giftcard.models import GiftCardTransaction
from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction
from loyalty.services import LoyaltyService
from order.checkout.quote import ExpectedTotal
from order.exceptions import InvalidOrderDataError, OrderTotalChangedError
from order.models import OrderVatLine
from order.models.order import Order
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from promotion.enum import BenefitType, PromotionTrigger
from promotion.factories import PromotionCodeFactory, PromotionFactory
from promotion.models import CartPromotionCode, PromotionRedemption
from shipping.exceptions import ShippingParcelTooLargeError
from shipping.interfaces import get_provider
from shipping_acs import config as acs_config
from tests.utils.fake_stripe import fake_stripe
from tests.utils.legal import checkout_acceptance
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


def _post_order(checkout, **extra):
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
    monkeypatch.setattr(
        throttle,
        "cache",
        LocMemCache(f"quote-throttle-{uuid4()}", {}),
        raising=False,
    )


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
        settings.AGENT_GATEWAY_INTERNAL_SECRET = "gateway-secret"
        _private_budget(monkeypatch, CartQuoteAnonThrottle, "2/minute")
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


class TestGiftCardCodesAreBudgeted:
    """Every route that prices a checkout says what a gift card settles
    (or that the code is no card). With codes, each also spends the
    balance check's budget, so none is a faster oracle than
    ``/giftcard/check``; without codes it is not counted."""

    CODES = {"gift_card_codes": ["GC-NOPE-NOPE"]}

    @pytest.fixture(autouse=True)
    def _two_a_minute(self, monkeypatch):
        _private_budget(monkeypatch, GiftCardCheckThrottle, "2/minute")

    def test_on_the_quote(self):
        checkout = _checkout()
        card = GiftCardFactory(initial_value=Money(Decimal("10.00"), "EUR"))
        with_card = {**_choices(checkout), "gift_card_codes": [card.code]}

        statuses = [
            _post_quote(checkout, **with_card).status_code for _ in range(3)
        ]

        assert statuses == [200, 200, 429]
        assert _post_quote(checkout, **_choices(checkout)).status_code == 200

    def test_on_the_payment_intent(self):
        checkout = _checkout()
        stripe = PayWayFactory.create_online_payment(provider_code="stripe")

        with fake_stripe():
            statuses = [
                _post_intent(checkout, stripe, **self.CODES).status_code
                for _ in range(3)
            ]
            without_codes = _post_intent(checkout, stripe)

        assert statuses == [400, 400, 429]
        assert without_codes.status_code == 200, without_codes.content

    def test_on_order_creation(self):
        checkout = _checkout()

        statuses = [
            _post_order(checkout, **self.CODES).status_code for _ in range(3)
        ]

        assert statuses == [400, 400, 429]
        assert not Order.objects.exists()
        assert _post_order(checkout).status_code == 201


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

        response = _post_order(checkout, loyalty_points_to_redeem=4)

        assert response.status_code == 400
        body = response.json()
        assert body["error"]["type"] == "invalid_order_data"
        assert "5" in body["fieldErrors"]["loyaltyPointsToRedeem"][0]
        assert not Order.objects.exists()
        assert LoyaltyService.get_user_balance(checkout.user) == 100
        checkout.product.refresh_from_db()
        assert checkout.product.stock == 10


class TestLivePrice:
    def test_a_polled_quote_asks_the_carrier_once(self):
        """ACS prices live. The price is cached per country, region and
        weight bracket (``AcsCarrier._fetch_live_quote``), the entry order
        creation reads, so polling a quote does not reach the carrier."""
        checkout = _checkout()
        enable_rate(
            checkout.country,
            provider_code="acs",
            price=SHIPPING,
            free_shipping_threshold=THRESHOLD,
        )
        acs = {**_choices(checkout), "shipping_provider_code": "acs"}
        cache.clear()

        with (
            _store_settings(ACS_DYNAMIC_PRICING_ENABLED=True),
            patch.object(acs_config, "is_configured", return_value=True),
            patch.object(acs_config, "station_origin", return_value="ΑΚ"),
            patch("shipping_acs.client.AcsClient") as client,
        ):
            client.return_value.price_calculation.return_value = {
                "Total_Ammount": 5.21
            }
            first = _quote(checkout, **acs)
            second = _quote(checkout, **acs)

        assert _money(first["shipping"]["price"]) == Decimal("5.21")
        assert second["shipping"] == first["shipping"]
        assert second["expectedTotal"] == first["expectedTotal"]
        assert client.return_value.price_calculation.call_count == 1


class TestExpectedTotal:
    """An order created with ``expectedTotal`` exists only at that total."""

    def test_the_quoted_total_creates_the_order(self):
        checkout = _checkout()
        quote = _quote(checkout, **_choices(checkout))

        response = _post_order(checkout, expected_total=quote["expectedTotal"])

        assert response.status_code == 201, response.content
        order = Order.objects.get()
        assert ExpectedTotal.of(order.amount_due) == ExpectedTotal(2500, "EUR")

    def test_no_expected_total_is_still_accepted(self):
        checkout = _checkout()

        response = _post_order(checkout)

        assert response.status_code == 201, response.content

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
            legal_acceptance=checkout_acceptance(),
            cart=checkout.cart,
            shipping_address=_address(checkout),
            pay_way=checkout.pay_way,
            user=checkout.user,
        )

    assert not Order.objects.exists()
    checkout.product.refresh_from_db()
    assert checkout.product.stock == 10
