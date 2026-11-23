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

import smtplib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import stripe
from celery.exceptions import Retry
from django.db import connection, transaction
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
from order.tasks import alert_payment_without_order_task
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from tests.utils.checkout import priced
from tests.utils.dispatch import apply_in
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


def _payment_id():
    return f"pi_{uuid.uuid4().hex}"


#: The schema the test runs in, which is what a dispatch pins.
CURRENT_STORE = SimpleNamespace(schema_name=connection.schema_name)


def _bound(purpose):
    return binding_metadata(purpose, uuid.uuid4())


def _webhook(payment_id, capture, metadata=None):
    """Deliver the event inside the webhook's transaction; return the
    ``apply_async`` of the alert task."""
    with (
        patch.object(alert_payment_without_order_task, "apply_async") as queued,
        capture(execute=True),
    ):
        with transaction.atomic():
            handle_stripe_payment_succeeded(
                sender=None, event=_succeeded(payment_id, metadata=metadata)
            )
    return queued


def _run_task(payment_id, *, throw=True):
    return apply_in(
        CURRENT_STORE,
        alert_payment_without_order_task,
        [],
        {"payment_id": payment_id, "amount": "50", "currency": "EUR"},
        task_id=str(uuid.uuid4()),
        throw=throw,
    )


class TestPaymentWithoutOrderAlert:
    def test_an_order_bound_payment_without_an_order_is_queued_at_once(
        self, django_capture_on_commit_callbacks
    ):
        payment_id = _payment_id()

        queued = _webhook(
            payment_id,
            django_capture_on_commit_callbacks,
            _bound(PaymentPurpose.ORDER),
        )

        queued.assert_called_once()
        assert queued.call_args.kwargs["countdown"] == 0
        assert queued.call_args.kwargs["kwargs"]["payment_id"] == payment_id

    def test_the_task_alerts_once_however_often_it_runs(self):
        payment_id = _payment_id()

        with patch("emails.send.send_email") as send:
            _run_task(payment_id)
            _run_task(payment_id)

        send.assert_called_once()
        key, alert = send.call_args.args
        assert key == "ops.payment_without_order"
        assert alert.payment_id == payment_id
        assert alert.amount == Money(Decimal(50), "EUR")

    def test_a_failed_send_is_retried_and_the_claim_is_free(self):
        payment_id = _payment_id()

        with (
            patch(
                "emails.send.send_email",
                side_effect=smtplib.SMTPServerDisconnected("gone"),
            ),
            pytest.raises(Retry),
        ):
            _run_task(payment_id)
        with patch("emails.send.send_email") as send:
            _run_task(payment_id)

        send.assert_called_once()

    def test_an_unbound_payment_is_logged_and_queues_nothing(
        self, django_capture_on_commit_callbacks, caplog
    ):
        with caplog.at_level("WARNING"):
            queued = _webhook(_payment_id(), django_capture_on_commit_callbacks)

        queued.assert_not_called()
        assert "is not bound to this store's orders" in caplog.text

    def test_a_payment_bound_to_another_store_queues_nothing(
        self, django_capture_on_commit_callbacks
    ):
        metadata = {
            **_bound(PaymentPurpose.ORDER),
            "tenant_schema": "elsewhere",
        }

        queued = _webhook(
            _payment_id(), django_capture_on_commit_callbacks, metadata
        )

        queued.assert_not_called()

    def test_a_rolled_back_webhook_queues_nothing(
        self, django_capture_on_commit_callbacks, monkeypatch
    ):
        # The suite runs on_commit callbacks at once; this test observes
        # the deferral, so it puts Django's own back.
        monkeypatch.setattr(
            transaction,
            "on_commit",
            lambda func, using=None, robust=False: connection.on_commit(
                func, robust=robust
            ),
        )
        metadata = _bound(PaymentPurpose.ORDER)

        with (
            patch.object(
                alert_payment_without_order_task, "apply_async"
            ) as queued,
            django_capture_on_commit_callbacks(execute=True),
        ):
            with pytest.raises(RuntimeError), transaction.atomic():
                handle_stripe_payment_succeeded(
                    sender=None,
                    event=_succeeded(_payment_id(), metadata=metadata),
                )
                raise RuntimeError("the webhook failed after the handler")
        queued.assert_not_called()

        with (
            patch.object(
                alert_payment_without_order_task, "apply_async"
            ) as queued,
            django_capture_on_commit_callbacks(execute=True),
        ):
            with transaction.atomic():
                handle_stripe_payment_succeeded(
                    sender=None,
                    event=_succeeded(_payment_id(), metadata=metadata),
                )
            queued.assert_not_called()  # nothing before the commit
        queued.assert_called_once()

    def test_a_gift_card_intent_is_not_an_orphan(
        self, django_capture_on_commit_callbacks
    ):
        payment_id = _payment_id()
        GiftCardPurchaseFactory(payment_id=payment_id)

        with patch("giftcard.services.GiftCardService.complete_purchase"):
            queued = _webhook(
                payment_id,
                django_capture_on_commit_callbacks,
                _bound(PaymentPurpose.GIFT_CARD),
            )

        queued.assert_not_called()

    def test_an_intent_bound_to_an_order_finds_it(
        self, django_capture_on_commit_callbacks
    ):
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id="pi_the_newer_one",
            pay_way=PayWayFactory.create_online_payment("stripe"),
        )

        queued = _webhook(
            _payment_id(),
            django_capture_on_commit_callbacks,
            binding_metadata(PaymentPurpose.ORDER, order.uuid),
        )

        queued.assert_not_called()

    def test_an_intent_an_order_carries_is_not_an_orphan(
        self, django_capture_on_commit_callbacks
    ):
        payment_id = _payment_id()
        OrderFactory(
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            pay_way=PayWayFactory.create_online_payment("stripe"),
        )

        queued = _webhook(
            payment_id,
            django_capture_on_commit_callbacks,
            _bound(PaymentPurpose.ORDER),
        )

        queued.assert_not_called()


class TestAPaymentFirstIntentWaitsForItsOrder:
    """A payment-first intent can succeed before ``POST /order`` has created
    its order, so its alert waits the grace and is sent only if no order
    carries it by then. Removed with the payment-first path (#818, R3)."""

    def test_the_alert_is_deferred_by_the_grace(
        self, django_capture_on_commit_callbacks
    ):
        payment_id = _payment_id()

        queued = _webhook(
            payment_id,
            django_capture_on_commit_callbacks,
            _bound(PaymentPurpose.CART),
        )

        queued.assert_called_once()
        assert queued.call_args.kwargs["countdown"] == 15 * 60

    def test_an_order_created_meanwhile_sends_nothing(self):
        payment_id = _payment_id()
        OrderFactory(
            num_order_items=0,
            payment_id=payment_id,
            pay_way=PayWayFactory.create_online_payment("stripe"),
        )

        with patch("emails.send.send_email") as send:
            _run_task(payment_id)

        send.assert_not_called()

    def test_no_order_after_the_grace_alerts_once(self):
        payment_id = _payment_id()

        with patch("emails.send.send_email") as send:
            _run_task(payment_id)
            _run_task(payment_id)

        send.assert_called_once()
