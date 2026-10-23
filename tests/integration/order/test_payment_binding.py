"""A payment is bound to what it was made for.

The attack: buy a Stripe gift card for exactly a cart's total, let the
webhook complete the card, then post the same PaymentIntent id to
``POST /api/v1/order``. The intent is real, paid and for the right amount,
so every check that only read its state, amount and currency accepted it:
one payment bought the card AND a paid order.

Every intent we create now carries server-set metadata naming its purpose,
its subject (the cart or purchase uuid) and the tenant, and the payment-first
order path refuses an intent whose metadata does not name THIS cart.
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.conf import settings
from django.urls import reverse
from djmoney.money import Money
from rest_framework.test import APIClient

from cart.models.cart import Cart
from cart.models.item import CartItem
from country.factories import CountryFactory
from giftcard.models import GiftCard, GiftCardPurchase
from giftcard.services import GiftCardService
from order.exceptions import PaymentVerificationError
from order.factories.order import OrderFactory
from order.models.order import Order
from order.payment import StripePaymentProvider
from order.payment_binding import PaymentPurpose
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from tests.utils.fake_stripe import fake_stripe
from tests.utils.shipping import enable_rate

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

GIFT_CARD_URL = reverse("giftcard:giftcard-purchase")
ORDER_URL = reverse("order-list")


@contextmanager
def _gift_cards():
    values = {
        "GIFT_CARDS_ENABLED": True,
        "GIFT_CARD_MIN_AMOUNT": 10,
        "GIFT_CARD_MAX_AMOUNT": 500,
    }
    with patch(
        "extra_settings.models.Setting.get",
        side_effect=lambda key, default=None: values.get(key, default),
    ):
        yield


@pytest.fixture
def stripe():
    with fake_stripe() as fake:
        yield fake


@pytest.fixture
def checkout(db):
    """A guest cart above the free-shipping threshold (the total carries
    no shipping) and a fee-free Stripe pay way."""
    country = CountryFactory.create()
    enable_rate(country)
    pay_way = PayWayFactory.create(
        provider_code="stripe",
        settlement=PaySettlement.ONLINE,
        active=True,
        cost=0,
    )
    product = ProductFactory.create(
        price=Money("65.00", settings.DEFAULT_CURRENCY),
        stock=20,
        active=True,
        num_images=0,
        num_reviews=0,
    )
    cart = Cart.objects.create(user=None)
    CartItem.objects.create(cart=cart, product=product, quantity=2)
    return cart, pay_way, country


def _address(country) -> dict:
    return {
        "first_name": "John",
        "last_name": "Doe",
        "email": "john@example.com",
        "street": "Main St",
        "street_number": "123",
        "city": "Athens",
        "zipcode": "12345",
        "country_id": country.alpha_2,
        "phone": "+306900000000",
        "shipping_kind": "home_delivery",
    }


def _cart_intent(stripe, cart, **overrides) -> str:
    """What ``cart_create_payment_intent`` does for ``cart``."""
    params = {
        "purpose": PaymentPurpose.CART,
        "subject": str(cart.uuid),
    }
    params.update(overrides)
    ok, data = StripePaymentProvider().process_payment(
        cart.total_price,
        order_id=f"cart_{cart.uuid}",
        **params,
    )
    assert ok, data
    return data["payment_id"]


def _create_order(checkout, intent_id: str) -> Order:
    cart, pay_way, country = checkout
    return OrderService.create_order_from_cart(
        cart=cart,
        shipping_address=_address(country),
        payment_intent_id=intent_id,
        pay_way=pay_way,
        user=None,
    )


def _buy_gift_card(stripe, amount: Decimal) -> GiftCardPurchase:
    """``POST /api/v1/giftcard/purchase`` over Stripe."""
    with _gift_cards():
        response = APIClient().post(
            GIFT_CARD_URL,
            {
                "amount": str(amount),
                "buyerEmail": "buyer@example.com",
                "recipientEmail": "friend@example.com",
                "paymentProvider": "stripe",
            },
            format="json",
        )
    assert response.status_code == 201, response.content
    return GiftCardPurchase.objects.get(uuid=response.json()["purchaseUuid"])


class TestOneIntentCannotBuyTwoThings:
    def test_a_paid_gift_card_intent_cannot_pay_an_order(
        self, stripe, checkout
    ):
        purchase = _buy_gift_card(stripe, checkout[0].total_price.amount)
        stripe.pay(purchase.payment_id)
        # ``payment_intent.succeeded`` completes the card.
        GiftCardService.complete_purchase(
            purchase, payment_id=purchase.payment_id
        )
        assert GiftCard.objects.filter(purchase=purchase).exists()

        cart, pay_way, country = checkout
        response = APIClient().post(
            ORDER_URL,
            {
                "paymentIntentId": purchase.payment_id,
                "payWayId": pay_way.id,
                "countryId": country.alpha_2,
                "shippingKind": "home_delivery",
                "firstName": "John",
                "lastName": "Doe",
                "email": "john@example.com",
                "street": "Main St",
                "streetNumber": "123",
                "city": "Athens",
                "zipcode": "12345",
                "phone": "+306900000000",
            },
            format="json",
            HTTP_X_CART_ID=str(cart.uuid),
        )

        assert response.status_code == 400, response.content
        assert not Order.objects.exists()
        assert cart.items.count() == 1, "the cart was not consumed"

    def test_the_same_attack_before_the_intent_is_paid(self, stripe, checkout):
        """Placing the order first and paying second was the variant: the
        order would be created PENDING and later settled by the poll."""
        purchase = _buy_gift_card(stripe, checkout[0].total_price.amount)

        with pytest.raises(PaymentVerificationError):
            _create_order(checkout, purchase.payment_id)

        assert not Order.objects.exists()

    def test_a_purchase_holding_the_intent_refuses_it_whatever_it_says(
        self, stripe, checkout
    ):
        """Metadata is the primary binding; the purchase row is the
        backstop, so an intent that somehow names the cart is still refused
        once a gift-card purchase holds it."""
        cart = checkout[0]
        intent_id = _cart_intent(stripe, cart)
        GiftCardPurchase.objects.create(
            buyer_email="buyer@example.com",
            amount=cart.total_price,
            recipient_email="friend@example.com",
            provider_code="stripe",
            payment_id=intent_id,
        )

        with pytest.raises(PaymentVerificationError, match="gift-card"):
            _create_order(checkout, intent_id)

        assert not Order.objects.exists()


class TestTheIntentMustNameThisCart:
    def test_an_intent_made_for_this_cart_creates_the_order(
        self, stripe, checkout
    ):
        intent_id = _cart_intent(stripe, checkout[0])

        order = _create_order(checkout, intent_id)

        assert order.payment_id == intent_id

    def test_another_carts_intent_is_refused(self, stripe, checkout):
        other = Cart.objects.create(user=None)
        intent_id = _cart_intent(stripe, other)

        with pytest.raises(PaymentVerificationError):
            _create_order(checkout, intent_id)

        assert not Order.objects.exists()

    def test_an_intent_made_for_an_existing_order_is_refused(
        self, stripe, checkout
    ):
        intent_id = _cart_intent(
            stripe,
            checkout[0],
            purpose=PaymentPurpose.ORDER,
            subject=str(checkout[0].uuid),
        )

        with pytest.raises(PaymentVerificationError):
            _create_order(checkout, intent_id)

    def test_another_tenants_intent_is_refused(self, stripe, checkout):
        intent_id = _cart_intent(stripe, checkout[0])
        stripe.intents[intent_id].metadata["tenant_schema"] = "another_store"

        with pytest.raises(PaymentVerificationError):
            _create_order(checkout, intent_id)

    def test_an_intent_with_no_binding_is_refused(self, stripe, checkout):
        intent_id = _cart_intent(stripe, checkout[0])
        stripe.intents[intent_id].metadata.clear()

        with pytest.raises(PaymentVerificationError):
            _create_order(checkout, intent_id)

    def test_the_refusal_does_not_leak_the_expected_uuid(
        self, stripe, checkout
    ):
        other = Cart.objects.create(user=None)
        intent_id = _cart_intent(stripe, other)

        with pytest.raises(PaymentVerificationError) as raised:
            _create_order(checkout, intent_id)

        assert str(checkout[0].uuid) not in str(raised.value)
        assert str(other.uuid) not in str(raised.value)


class TestEveryIntentWeCreateIsBound:
    def test_the_cart_endpoint_binds_the_intent_to_the_cart(
        self, stripe, checkout
    ):
        cart = checkout[0]
        intent_id = _cart_intent(stripe, cart)

        assert stripe.intents[intent_id].metadata == {
            "order_id": f"cart_{cart.uuid}",
            "source": "django_app",
            "tenant_schema": "public",
            "purpose": "cart",
            "subject": str(cart.uuid),
        }

    def test_the_gift_card_purchase_binds_the_intent_to_the_purchase(
        self, stripe
    ):
        purchase = _buy_gift_card(stripe, Decimal("25.00"))

        metadata = stripe.intents[purchase.payment_id].metadata
        assert metadata["purpose"] == "giftcard"
        assert metadata["subject"] == str(purchase.uuid)
        assert metadata["tenant_schema"] == "public"

    def test_a_caller_cannot_leave_the_binding_out(self, stripe):
        with pytest.raises(KeyError):
            StripePaymentProvider().process_payment(
                Money("10.00", settings.DEFAULT_CURRENCY), order_id="x"
            )


class TestTheReverseAndTheOtherIntentKinds:
    def test_a_checkout_session_binds_its_intent_to_the_order(self, stripe):
        with patch("order.payment.stripe.checkout.Session.create") as create:
            create.return_value.id = "cs_1"
            create.return_value.url = "https://checkout.example/cs_1"
            create.return_value.expires_at = 1_800_000_000
            ok, _data = StripePaymentProvider().create_checkout_session(
                Money("10.00", settings.DEFAULT_CURRENCY),
                "7",
                success_url="https://shop/ok",
                cancel_url="https://shop/no",
                purpose=PaymentPurpose.ORDER,
                subject="order-uuid",
            )

        assert ok
        params = create.call_args.kwargs
        for metadata in (
            params["metadata"],
            params["payment_intent_data"]["metadata"],
        ):
            assert metadata["purpose"] == "order"
            assert metadata["subject"] == "order-uuid"
            assert metadata["tenant_schema"] == "public"

    def test_an_intent_an_order_also_holds_issues_no_gift_card(
        self, stripe, checkout
    ):
        from unittest.mock import Mock

        from djstripe.models import Event

        from order.signals.handlers import handle_stripe_payment_succeeded

        purchase = _buy_gift_card(stripe, Decimal("25.00"))
        OrderFactory(payment_id=purchase.payment_id, num_order_items=0)
        event = Mock(spec=Event)
        event.id = "evt_both"
        event.data = {
            "object": {
                "id": purchase.payment_id,
                "amount_received": 2500,
                "currency": "eur",
            }
        }

        handle_stripe_payment_succeeded(sender=None, event=event)

        assert not GiftCard.objects.filter(purchase=purchase).exists()
