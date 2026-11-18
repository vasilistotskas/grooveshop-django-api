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
from django.urls import reverse
from djmoney.money import Money
from knox.models import get_token_model
from rest_framework.test import APIClient

from cart.factories import CartFactory, CartItemFactory
from cart.models import Cart
from country.factories import CountryFactory
from order.checkout.quote import ExpectedTotal
from order.exceptions import InvalidOrderDataError, OrderTotalChangedError
from order.models.order import Order
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from promotion.enum import BenefitType, PromotionTrigger
from promotion.factories import PromotionCodeFactory, PromotionFactory
from promotion.models import CartPromotionCode, PromotionRedemption
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


@pytest.fixture(autouse=True)
def _promotions_on():
    def _get(key, default=None):
        return {"PROMOTIONS_ENABLED": True}.get(key, default)

    with patch("extra_settings.models.Setting.get", side_effect=_get):
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
    cart = CartFactory(user=owner)
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

    def test_nothing_chosen(self):
        checkout = _checkout()

        response = _post_quote(checkout)

        assert response.status_code == 200, response.content
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

    def test_both_chosen_is_a_total(self):
        checkout = _checkout()

        response = _post_quote(checkout, **_choices(checkout))

        assert response.status_code == 200, response.content
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

    def test_an_empty_cart(self):
        checkout = _checkout()
        checkout.cart.items.all().delete()

        response = _post_quote(checkout)

        assert response.status_code == 400
        assert response.json()["code"] == "cart_empty"

    def test_a_delivery_the_store_does_not_offer(self):
        checkout = _checkout()
        elsewhere = CountryFactory()

        response = _post_quote(
            checkout,
            **{**_choices(checkout), "country_id": elsewhere.alpha_2},
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


class TestCartToken:
    def test_a_token_quotes_the_owners_cart(self):
        """The shopping assistant asks anonymously, with the signed-in
        shopper's cart token: the quote is that cart's, at its owner's
        prices."""
        checkout = _checkout()
        _auth, raw = get_token_model().objects.create(checkout.user)
        shopper = APIClient()
        shopper.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        token = shopper.post(reverse("cart-agent-token")).data["token"]
        assistant = APIClient()
        assistant.credentials(HTTP_X_CART_TOKEN=token)

        response = assistant.post(
            reverse("cart-quote"), _choices(checkout), format="json"
        )

        assert response.status_code == 200, response.content
        assert _money(response.json()["amountDue"]) == TOTAL


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

    def test_another_total_is_409_with_the_quote(self):
        checkout = _checkout()

        response = _post_order(
            checkout, expected_total={"amount": 2499, "currency": "EUR"}
        )

        assert response.status_code == 409, response.content
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
