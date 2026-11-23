"""A Stripe order is created first and paid afterwards (#818).

An online pay way whose provider can mint a client-confirmed intent
(Stripe) takes the same path as Viva: ``POST /order`` without a payment
intent creates the order PENDING, keeps the cart, defers the courier
voucher, and the payment is opened by ``create_checkout_session`` or
``create_payment_intent``. Deductions covering the whole total settle it
at creation, as for any pay way.

``TestPaymentWithoutOrderAlert`` covers the guard beside it: a succeeded
PaymentIntent no order carries pages the store's staff, once.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
import stripe
from django.urls import reverse
from djmoney.money import Money
from djstripe.models import Event
from rest_framework.test import APIClient

from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from giftcard.factories import GiftCardFactory, GiftCardPurchaseFactory
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.payments.payment import StripePaymentProvider
from order.payments.payment_binding import PaymentPurpose, binding_metadata
from order.services import OrderService
from order.signals.handlers import handle_stripe_payment_succeeded
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from tests.utils.checkout import priced
from tests.utils.legal import checkout_acceptance
from tests.utils.shipping import enable_rate
from user.factories import UserAccountFactory
from vat.factories import VatFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

GIFT_CARD_SETTINGS = {
    "GIFT_CARDS_ENABLED": True,
    "GIFT_CARD_VALIDITY_DAYS": 1825,
}


@pytest.fixture
def enable_gift_cards():
    def _get(key, default=None):
        return GIFT_CARD_SETTINGS.get(key, default)

    with patch("extra_settings.models.Setting.get", side_effect=_get):
        yield


@pytest.fixture(autouse=True)
def _stripe_configured():
    with (
        patch(
            "tenant.credentials.stripe_credentials",
            return_value={
                "secret_key": "sk_test_dummy",
                "publishable_key": "pk_test_dummy",
                "live_mode": False,
            },
        ),
        patch(
            "pay_way.services.PayWayService.is_provider_configured",
            return_value=True,
        ),
    ):
        yield


@pytest.fixture
def checkout():
    user = UserAccountFactory(num_addresses=0)
    country = CountryFactory()
    enable_rate(country)
    cart = CartFactory(user=user)
    cart.items.all().delete()
    product = ProductFactory(
        stock=10,
        price=Money(Decimal(100), "EUR"),
        discount_percent=Decimal(0),
        vat=VatFactory(value=0),
        active=True,
    )
    CartItemFactory(cart=cart, product=product, quantity=1)
    pay_way = PayWayFactory(
        provider_code="stripe",
        settlement=PaySettlement.ONLINE,
        cost=Money(Decimal(0), "EUR"),
        free_threshold=Money(Decimal(0), "EUR"),
    )
    shipping_address = {
        "first_name": "Nikos",
        "last_name": "Ioannou",
        "email": "nikos@example.com",
        "street": "Stadiou",
        "street_number": "5",
        "city": "Athens",
        "zipcode": "10564",
        "country_id": country.alpha_2,
        "phone": "+306900000001",
    }
    return {
        "user": user,
        "cart": cart,
        "pay_way": pay_way,
        "shipping_address": shipping_address,
    }


def _create_order(checkout, codes=None):
    with patch.object(
        OrderService, "_dispatch_shipment_creation_task"
    ) as dispatch:
        order = OrderService.create_order_from_cart_offline(
            **priced(
                legal_acceptance=checkout_acceptance(),
                cart=checkout["cart"],
                shipping_address=checkout["shipping_address"],
                pay_way=checkout["pay_way"],
                user=checkout["user"],
                gift_card_codes=codes,
            )
        )
    order.refresh_from_db()
    return order, dispatch


def _client(user):
    client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
    client.force_authenticate(user=user)
    return client


class TestAStripeOrderWithoutAnIntent:
    def test_is_created_pending_and_keeps_the_cart(self, checkout):
        order, dispatch = _create_order(checkout)

        assert order.status == OrderStatus.PENDING
        assert order.payment_status == PaymentStatus.PENDING
        assert not order.payment_id
        assert order.awaits_online_payment
        assert checkout["cart"].items.count() == 1
        # The courier voucher waits for the payment.
        dispatch.assert_not_called()

    def test_a_gift_card_remainder_is_left_to_pay(
        self, enable_gift_cards, checkout
    ):
        card = GiftCardFactory(initial_value=Money(Decimal(30), "EUR"))

        order, dispatch = _create_order(checkout, [card.code])

        assert order.gift_card_amount.amount == Decimal("30.00")
        assert order.amount_due.amount == order.total_price.amount - Decimal(
            "30.00"
        )
        assert order.amount_due.amount > 0
        assert order.status == OrderStatus.PENDING
        assert order.payment_status == PaymentStatus.PENDING
        assert checkout["cart"].items.count() == 1
        dispatch.assert_not_called()

    def test_a_full_deduction_cover_settles_at_creation(
        self, enable_gift_cards, checkout
    ):
        card = GiftCardFactory(initial_value=Money(Decimal(500), "EUR"))

        order, dispatch = _create_order(checkout, [card.code])

        assert order.amount_due.amount == Decimal(0)
        assert order.payment_status == PaymentStatus.COMPLETED
        assert order.payment_id == f"GIFTCARD_{order.uuid}"
        assert checkout["cart"].items.count() == 0
        dispatch.assert_called_once()


class TestThePaymentIsOpenedAfterwards:
    def _remainder_order(self, enable_gift_cards, checkout):
        card = GiftCardFactory(initial_value=Money(Decimal(30), "EUR"))
        order, _dispatch = _create_order(checkout, [card.code])
        return order

    def test_create_checkout_session_charges_the_remainder(
        self, enable_gift_cards, checkout
    ):
        order = self._remainder_order(enable_gift_cards, checkout)
        session = Mock(id="cs_818", url="https://checkout.stripe.com/c/cs_818")
        session.expires_at = int(
            (datetime.now(UTC) + timedelta(hours=1)).timestamp()
        )

        with (
            patch.object(
                StripePaymentProvider,
                "create_checkout_session",
                autospec=True,
                side_effect=StripePaymentProvider.create_checkout_session,
            ) as opened,
            patch.object(
                stripe.checkout.Session, "create", return_value=session
            ),
        ):
            response = _client(checkout["user"]).post(
                reverse(
                    "order-create-checkout-session", kwargs={"pk": order.pk}
                ),
                {
                    "successUrl": "https://shop.example/ok",
                    "cancelUrl": "https://shop.example/no",
                },
                format="json",
            )

        assert response.status_code == 200, response.data
        assert response.data["session_id"] == "cs_818"
        assert opened.call_args.kwargs["amount"] == order.amount_due
        order.refresh_from_db()
        assert order.metadata["stripe_checkout_session_id"] == "cs_818"

    def test_create_payment_intent_charges_the_remainder(
        self, enable_gift_cards, checkout
    ):
        order = self._remainder_order(enable_gift_cards, checkout)
        intent = Mock(
            id="pi_818",
            status="requires_payment_method",
            client_secret="pi_818_secret",
            next_action=None,
        )

        with patch.object(
            stripe.PaymentIntent, "create", return_value=intent
        ) as create:
            response = _client(checkout["user"]).post(
                reverse("order-create-payment-intent", kwargs={"pk": order.pk}),
                {},
                format="json",
            )

        assert response.status_code == 200, response.data
        sent = create.call_args.kwargs
        assert sent["amount"] == round(order.amount_due.amount * 100)
        order.refresh_from_db()
        assert order.payment_id == "pi_818"


def _succeeded(payment_id, *, metadata=None):
    event = Mock(spec=Event)
    event.id = f"evt_{uuid.uuid4().hex}"
    event.type = "payment_intent.succeeded"
    event.data = {
        "object": {
            "id": payment_id,
            "amount": 5000,
            "amount_received": 5000,
            "currency": "eur",
            "status": "succeeded",
            "metadata": metadata or {},
        }
    }
    return event


def _deliver(event):
    with patch("emails.send.send_email") as send:
        handle_stripe_payment_succeeded(sender=None, event=event)
    return send


def _orphan_alerts(send):
    """The payment-without-order alerts among what was sent: a payment of
    another amount than its order's sends the mismatch alert too."""
    return [
        call
        for call in send.call_args_list
        if call.args[0] == "ops.payment_without_order"
    ]


def _payment_id():
    return f"pi_{uuid.uuid4().hex}"


class TestPaymentWithoutOrderAlert:
    def test_an_orphan_intent_pages_staff_once(self):
        payment_id = _payment_id()

        send = _deliver(_succeeded(payment_id))

        send.assert_called_once()
        key, alert = send.call_args.args
        assert key == "ops.payment_without_order"
        assert alert.payment_id == payment_id
        assert alert.amount == Money(Decimal(50), "EUR")

    def test_a_redelivery_sends_nothing_more(self):
        payment_id = _payment_id()

        first = _deliver(_succeeded(payment_id))
        again = _deliver(_succeeded(payment_id))

        first.assert_called_once()
        again.assert_not_called()

    def test_a_failed_send_does_not_raise_and_is_tried_again(self):
        payment_id = _payment_id()

        with patch(
            "emails.send.send_email", side_effect=RuntimeError("smtp down")
        ):
            handle_stripe_payment_succeeded(
                sender=None, event=_succeeded(payment_id)
            )
        retried = _deliver(_succeeded(payment_id))

        retried.assert_called_once()

    def test_a_gift_card_intent_is_not_an_orphan(self):
        payment_id = _payment_id()
        GiftCardPurchaseFactory(payment_id=payment_id)

        with patch("giftcard.services.GiftCardService.complete_purchase"):
            send = _deliver(_succeeded(payment_id))

        send.assert_not_called()

    def test_an_intent_bound_to_an_order_is_not_an_orphan(self):
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id="pi_the_newer_one",
            pay_way=PayWayFactory.create_online_payment("stripe"),
        )
        older = _payment_id()

        send = _deliver(
            _succeeded(
                older,
                metadata=binding_metadata(PaymentPurpose.ORDER, order.uuid),
            )
        )

        assert not _orphan_alerts(send)

    def test_an_intent_an_order_carries_is_not_an_orphan(self):
        payment_id = _payment_id()
        OrderFactory(
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            pay_way=PayWayFactory.create_online_payment("stripe"),
        )

        send = _deliver(_succeeded(payment_id))

        assert not _orphan_alerts(send)
