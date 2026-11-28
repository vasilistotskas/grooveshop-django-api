"""A payment is bound to what it was made for.

The attack this closed: buy a Stripe gift card for exactly a cart's total,
let the webhook complete the card, then post the same PaymentIntent id to
``POST /api/v1/order``. The intent was real, paid and for the right amount,
so one payment bought the card AND a paid order.

Every intent we create carries server-set metadata naming its purpose, its
subject (the order or purchase uuid) and the tenant. Orders are created
before any payment (#818), so an order never takes an intent from the
request: the webhook finds an order's intent by its own ``payment_id`` or
by an ORDER binding of this store, and nothing else.
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
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.payments.payment import StripePaymentProvider
from order.payments.payment_binding import PaymentPurpose, binding_metadata
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from tests.utils.checkout import quoted_body
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


def _succeeded(payment_id: str, metadata: dict | None = None):
    """``OrderService.handle_payment_succeeded`` for a collected intent."""
    return OrderService.handle_payment_succeeded(
        payment_id,
        captured=Money("50.00", settings.DEFAULT_CURRENCY),
        metadata=metadata,
    )


def _unpaid_order():
    return OrderFactory(
        num_order_items=0,
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        payment_id="",
        pay_way=PayWayFactory.create_online_payment(),
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
    assert response.status_code == 200, response.content
    return GiftCardPurchase.objects.get(uuid=response.json()["purchaseUuid"])


class TestOneIntentCannotBuyTwoThings:
    @pytest.mark.usefixtures("checkout_legal_documents")
    def test_a_paid_gift_card_intent_cannot_pay_an_order(
        self, stripe, checkout
    ):
        """The id in the body is not read: the order is created unpaid,
        holding no payment, and the intent stays the purchase's."""
        purchase = _buy_gift_card(stripe, checkout[0].total_price.amount)
        stripe.pay(purchase.payment_id)
        # ``payment_intent.succeeded`` completes the card.
        GiftCardService.complete_purchase(
            purchase, payment_id=purchase.payment_id
        )
        assert GiftCard.objects.filter(purchase=purchase).exists()

        cart, pay_way, country = checkout
        client = APIClient()
        headers = {"HTTP_X_CART_ID": str(cart.uuid)}
        body = {
            "paymentIntentId": purchase.payment_id,
            "termsAccepted": True,
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
        }
        response = client.post(
            ORDER_URL,
            quoted_body(client, body, **headers),
            format="json",
            **headers,
        )

        assert response.status_code == 201, response.content
        order = Order.objects.get(pk=response.json()["id"])
        assert order.payment_status == PaymentStatus.PENDING
        assert not order.payment_id
        assert not order.is_paid
        assert not Order.objects.filter(payment_id=purchase.payment_id).exists()

    def test_a_gift_card_intent_naming_an_order_does_not_pay_it(self):
        """A gift-card binding is never read as an order's, even when its
        subject is that order's uuid."""
        order = _unpaid_order()

        outcome = _succeeded(
            "pi_gift_card_named_order",
            binding_metadata(PaymentPurpose.GIFT_CARD, order.uuid),
        )

        assert outcome is None
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING
        assert not order.payment_id


class TestAnOrderIsFoundOnlyByItsOwnBinding:
    def test_an_order_binding_of_this_store_finds_the_order(self):
        order = _unpaid_order()

        outcome = _succeeded(
            "pi_bound_to_order",
            binding_metadata(PaymentPurpose.ORDER, order.uuid),
        )

        assert outcome is not None
        assert outcome.order == order

    def test_another_stores_order_binding_finds_nothing(self):
        order = _unpaid_order()
        metadata = {
            **binding_metadata(PaymentPurpose.ORDER, order.uuid),
            "tenant_schema": "another_store",
        }

        assert _succeeded("pi_other_store", metadata) is None
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING

    @pytest.mark.parametrize("subject", ["", "not-a-uuid"])
    def test_a_binding_with_no_order_uuid_finds_nothing(self, subject):
        _unpaid_order()
        metadata = binding_metadata(PaymentPurpose.ORDER, subject)

        assert _succeeded("pi_no_subject", metadata) is None
        assert not Order.objects.filter(
            payment_status=PaymentStatus.COMPLETED
        ).exists()


class TestEveryIntentWeCreateIsBound:
    def test_the_gift_card_purchase_binds_the_intent_to_the_purchase(
        self, stripe
    ):
        purchase = _buy_gift_card(stripe, Decimal("25.00"))

        metadata = stripe.intents[purchase.payment_id].metadata
        assert metadata["purpose"] == "giftcard"
        assert metadata["subject"] == str(purchase.uuid)
        assert metadata["tenant_schema"] == "public"

    def test_a_caller_cannot_leave_the_binding_out(self, stripe):
        with pytest.raises(TypeError):
            StripePaymentProvider().process_payment(
                Money("10.00", settings.DEFAULT_CURRENCY),
                order_id="x",
                order_uuid="x",
                customer_email="",
            )


class TestTheReverseAndTheOtherIntentKinds:
    def test_a_checkout_session_binds_its_intent_to_the_order(self, stripe):
        with patch(
            "order.payments.payment.stripe.checkout.Session.create"
        ) as create:
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
