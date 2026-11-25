"""An unpaid online order holds its stock exactly as long as it can be paid.

The payment link or session is created with an expiry, the provider
reports the expiry it applied, and ``Order.payment_hold_expires_at``
stores it. ``auto_cancel_stuck_pending_orders`` releases the stock only
once that instant (plus a grace window) has passed, so a payment can never
succeed after the stock was released.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
import stripe
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from djmoney.money import Money
from extra_settings.models import Setting
from rest_framework.test import APIClient

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.inventory.stock import StockManager
from order.models.order import Order
from order.payments import payment_hold
from order.payments.payment import (
    StripePaymentProvider,
    VivaWalletPaymentProvider,
)
from order.payments.payment_binding import PaymentPurpose
from order.payments.viva_settlement import handle_payment_created
from order.tasks import auto_cancel_stuck_pending_orders
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from tests.utils.viva import FakeVivaApi, verified_payment
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

HOLD = payment_hold.HOLD_SETTING
GRACE = payment_hold.GRACE_SETTING
ALERT = payment_hold.ALERT_SETTING
MAX_HOLD = payment_hold.MAX_HOLD_SETTING


def _set(name: str, value: int) -> None:
    Setting.objects.update_or_create(
        name=name,
        defaults={"value_type": "int", "value_int": value},
    )


@pytest.fixture(autouse=True)
def _short_windows():
    """30 minutes' hold and 5 minutes' grace, as shipped."""
    _set(HOLD, 30)
    _set(GRACE, 5)
    _set(MAX_HOLD, 60)
    _set(ALERT, 0)


def _unpaid(*, provider="viva_wallet", **kwargs):
    kwargs.setdefault("status", OrderStatus.PENDING)
    kwargs.setdefault("payment_status", PaymentStatus.PENDING)
    kwargs.setdefault("pay_way", PayWayFactory.create_online_payment(provider))
    return OrderFactory(num_order_items=0, **kwargs)


def _age(order, *, created=None, hold_expires=None):
    fields = {}
    if created is not None:
        fields["created_at"] = created
    if hold_expires is not None:
        fields["payment_hold_expires_at"] = hold_expires
    Order.objects.filter(pk=order.pk).update(**fields)
    order.refresh_from_db()


def _provider_with_stripe_key():
    return patch(
        "tenant.credentials.stripe_credentials",
        return_value={
            "secret_key": "sk_test_dummy",
            "publishable_key": "pk_test_dummy",
            "live_mode": False,
        },
    )


def _provider_with_viva_credentials():
    return patch(
        "tenant.credentials.viva_wallet_credentials",
        return_value={
            "merchant_id": "m",
            "api_key": "k",
            "client_id": "c",
            "client_secret": "s",
            "source_code": "Default",
            "live_mode": False,
        },
    )


def _viva_provider():
    with _provider_with_viva_credentials():
        return VivaWalletPaymentProvider()


class TestTheExpiryReachesTheProvider:
    """The window is passed to Stripe and Viva when the payment is created."""

    def test_stripe_session_expires_when_the_hold_ends(self):
        now = timezone.now()
        asked = now + timedelta(hours=2)
        session = Mock(id="cs_1", url="https://stripe/cs_1")
        session.expires_at = int(asked.timestamp())
        with (
            _provider_with_stripe_key(),
            patch(
                "order.payments.payment.stripe.checkout.Session.create",
                return_value=session,
            ) as create,
        ):
            ok, data = StripePaymentProvider().create_checkout_session(
                Money(Decimal(10), settings.DEFAULT_CURRENCY),
                "7",
                success_url="https://shop/ok",
                cancel_url="https://shop/no",
                purpose=PaymentPurpose.ORDER,
                subject="u",
                expires_at=asked,
            )

        assert ok
        assert create.call_args.kwargs["expires_at"] == int(asked.timestamp())
        assert data["expires_at"] == datetime.fromtimestamp(
            session.expires_at, tz=UTC
        )

    def test_stripe_never_gets_a_session_shorter_than_its_minimum(self):
        """Stripe refuses under 30 minutes; the 30 minute default hold
        must still be accepted, and the order held for what Stripe applies."""
        now = timezone.now()
        session = Mock(id="cs_1", url="https://stripe/cs_1")
        session.expires_at = int((now + timedelta(minutes=31)).timestamp())
        with (
            _provider_with_stripe_key(),
            patch(
                "order.payments.payment.stripe.checkout.Session.create",
                return_value=session,
            ) as create,
        ):
            StripePaymentProvider().create_checkout_session(
                Money(Decimal(10), settings.DEFAULT_CURRENCY),
                "7",
                success_url="https://shop/ok",
                cancel_url="https://shop/no",
                purpose=PaymentPurpose.ORDER,
                subject="u",
                expires_at=now + timedelta(minutes=30),
            )

        sent = create.call_args.kwargs["expires_at"]
        assert sent >= (now + timedelta(minutes=30)).timestamp() + 30

    def test_viva_payment_order_gets_a_timeout_in_seconds(self):
        provider = _viva_provider()
        response = Mock()
        response.json.return_value = {"orderCode": 123456}
        with (
            patch.object(provider, "_get_access_token", return_value="t"),
            patch("requests.post", return_value=response) as post,
        ):
            ok, data = provider.create_checkout_session(
                Money(Decimal(10), settings.DEFAULT_CURRENCY),
                "7",
                order_uuid="u",
                expires_at=timezone.now() + timedelta(minutes=30),
            )

        assert ok
        # Within a second of 30 minutes: the clock moved between the two reads.
        assert 1799 <= post.call_args.kwargs["json"]["paymentTimeout"] <= 1800
        assert data["expires_at"] > timezone.now() + timedelta(minutes=29)

    def test_viva_payment_order_without_a_window_keeps_vivas_default(self):
        provider = _viva_provider()
        response = Mock()
        response.json.return_value = {"orderCode": 123456}
        with (
            patch.object(provider, "_get_access_token", return_value="t"),
            patch("requests.post", return_value=response) as post,
        ):
            _ok, data = provider.create_checkout_session(
                Money(Decimal(10), settings.DEFAULT_CURRENCY),
                "7",
                order_uuid="u",
            )

        assert "paymentTimeout" not in post.call_args.kwargs["json"]
        assert "expires_at" not in data


class TestEveryCreationPathCarriesTheExpiry:
    """Every order is paid through its hosted checkout session (Stripe or
    Viva), which carries the hold's end as its expiry."""

    @staticmethod
    def _client(user):
        client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
        client.force_authenticate(user=user)
        return client

    @staticmethod
    def _configured():
        return patch(
            "pay_way.services.PayWayService.is_provider_configured",
            return_value=True,
        )

    @staticmethod
    def _provider(provider_code: str, applied):
        provider = Mock()
        provider.create_checkout_session.return_value = (
            True,
            {
                "session_id": "S1",
                "checkout_url": "https://pay.example/S1",
                "status": "created",
                "amount": "10.00",
                "currency": "EUR",
                "provider": provider_code,
                "expires_at": applied,
            },
        )
        provider.checkout_session_params.return_value = {}
        # No link of the order is payable yet, so one is opened (#643).
        provider.live_checkout_session.return_value = None
        provider.recorded_checkout_session.return_value = None
        provider.min_hosted_session = timedelta(0)
        return provider

    def _ask(self, order, user, provider):
        with (
            self._configured(),
            patch(
                "order.views.order.get_payment_provider", return_value=provider
            ),
        ):
            return self._client(user).post(
                reverse(
                    "order-create-checkout-session", kwargs={"pk": order.pk}
                ),
                {
                    "successUrl": "https://shop.example/ok",
                    "cancelUrl": "https://shop.example/no",
                },
                format="json",
            )

    @pytest.mark.parametrize("provider_code", ["stripe", "viva_wallet"])
    def test_hosted_checkout_session(self, provider_code):
        user = UserAccountFactory(num_addresses=0)
        order = _unpaid(user=user, provider=provider_code)
        applied = timezone.now() + timedelta(minutes=31)
        provider = self._provider(provider_code, applied)

        response = self._ask(order, user, provider)

        assert response.status_code == 200, response.data
        asked = provider.create_checkout_session.call_args.kwargs["expires_at"]
        assert (
            timedelta(minutes=29)
            < asked - timezone.now()
            <= timedelta(minutes=30)
        )
        order.refresh_from_db()
        # The expiry the provider applied is the one stored.
        assert order.payment_hold_expires_at == applied

    def test_the_per_store_hold_is_respected(self):
        _set(HOLD, 120)
        _set(MAX_HOLD, 240)
        user = UserAccountFactory(num_addresses=0)
        order = _unpaid(user=user, provider="stripe")
        provider = self._provider(
            "stripe", timezone.now() + timedelta(minutes=120)
        )

        response = self._ask(order, user, provider)

        assert response.status_code == 200, response.data
        asked = provider.create_checkout_session.call_args.kwargs["expires_at"]
        assert (
            timedelta(minutes=119)
            < asked - timezone.now()
            <= timedelta(minutes=120)
        )


class TestTheSweepWaitsForTheLinkToExpire:
    @staticmethod
    def _stocked_order(**kwargs):
        product = ProductFactory(stock=10)
        order = _unpaid(**kwargs)
        order.items.create(product=product, price=product.price, quantity=3)
        return order, product

    def test_released_after_the_hold_and_the_grace(self):
        now = timezone.now()
        order = _unpaid()
        _age(order, hold_expires=now - timedelta(minutes=6))

        result = auto_cancel_stuck_pending_orders()

        assert result["canceled_pending"] == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED

    def test_kept_while_the_link_can_still_be_paid(self):
        order = _unpaid()
        _age(order, hold_expires=timezone.now() + timedelta(minutes=1))

        assert auto_cancel_stuck_pending_orders()["canceled_pending"] == 0
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING

    def test_kept_through_the_grace_window(self):
        order = _unpaid()
        _age(order, hold_expires=timezone.now() - timedelta(minutes=4))

        assert auto_cancel_stuck_pending_orders()["canceled_pending"] == 0
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING

    def test_a_new_link_that_extends_the_hold_keeps_the_order(self):
        """Created long ago, but a fresh link is still payable."""
        order = _unpaid()
        _age(
            order,
            created=timezone.now() - timedelta(hours=3),
            hold_expires=timezone.now() + timedelta(minutes=20),
        )

        assert auto_cancel_stuck_pending_orders()["canceled_pending"] == 0

    def test_with_no_link_the_hold_runs_from_the_order(self):
        old = _unpaid()
        _age(old, created=timezone.now() - timedelta(minutes=36))
        fresh = _unpaid()
        _age(fresh, created=timezone.now() - timedelta(minutes=34))

        assert auto_cancel_stuck_pending_orders()["canceled_pending"] == 1
        old.refresh_from_db()
        fresh.refresh_from_db()
        assert old.status == OrderStatus.CANCELED
        assert fresh.status == OrderStatus.PENDING

    def test_the_store_chooses_the_hold(self):
        _set(HOLD, 120)
        _set(MAX_HOLD, 240)
        order = _unpaid()
        _age(order, created=timezone.now() - timedelta(minutes=60))

        assert auto_cancel_stuck_pending_orders()["canceled_pending"] == 0
        _set(HOLD, 30)
        assert auto_cancel_stuck_pending_orders()["canceled_pending"] == 1

    def test_stock_comes_back_with_the_cancel(self):
        order, product = self._stocked_order()
        _age(order, hold_expires=timezone.now() - timedelta(minutes=6))
        StockManager.decrement_stock(
            product_id=product.id,
            quantity=3,
            order_id=order.id,
            reason="test_setup",
        )
        product.refresh_from_db()
        assert product.stock == 7

        auto_cancel_stuck_pending_orders()

        product.refresh_from_db()
        assert product.stock == 10

    def test_cash_on_delivery_is_never_swept(self):
        order = _unpaid(
            pay_way=PayWayFactory(
                settlement=PaySettlement.COURIER_CASH, active=True
            )
        )
        _age(order, created=timezone.now() - timedelta(days=5))

        assert auto_cancel_stuck_pending_orders()["canceled_pending"] == 0

    def test_a_paid_order_is_never_swept(self):
        order = _unpaid(
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
        )
        _age(order, created=timezone.now() - timedelta(days=5))

        assert auto_cancel_stuck_pending_orders()["canceled_pending"] == 0


class TestAPaymentIntentIsClosedBeforeTheStockIsReleased:
    def test_the_intent_is_canceled_then_the_order(self):
        order = _unpaid(provider="stripe", payment_id="pi_open")
        _age(order, hold_expires=timezone.now() - timedelta(minutes=6))
        with (
            _provider_with_stripe_key(),
            patch(
                "order.payments.payment.stripe.PaymentIntent.retrieve",
                return_value=Mock(status="requires_payment_method"),
            ),
            patch(
                "order.payments.payment.stripe.PaymentIntent.cancel",
                return_value=Mock(status="canceled"),
            ) as cancel,
        ):
            result = auto_cancel_stuck_pending_orders()

        assert cancel.call_args.args == ("pi_open",)
        assert result["canceled_pending"] == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED

    def test_a_payment_already_processing_keeps_the_order_open(self):
        """Stripe refuses to cancel an intent that is past the point of
        no return; its webhook, not the sweep, settles the order."""
        order = _unpaid(provider="stripe", payment_id="pi_busy")
        _age(order, hold_expires=timezone.now() - timedelta(minutes=6))
        with (
            _provider_with_stripe_key(),
            patch(
                "order.payments.payment.stripe.PaymentIntent.cancel",
                side_effect=stripe.InvalidRequestError("busy", "id"),
            ),
            patch(
                "order.payments.payment.stripe.PaymentIntent.retrieve",
                return_value=Mock(status="processing"),
            ),
        ):
            result = auto_cancel_stuck_pending_orders()

        assert result["canceled_pending"] == 0
        assert result["skipped_open"] == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING

    def test_a_provider_outage_leaves_the_order_for_the_next_sweep(self):
        order = _unpaid(provider="stripe", payment_id="pi_open")
        _age(order, hold_expires=timezone.now() - timedelta(minutes=6))
        with (
            _provider_with_stripe_key(),
            patch(
                "order.payments.payment.stripe.PaymentIntent.retrieve",
                side_effect=stripe.APIConnectionError("down"),
            ),
            patch(
                "order.payments.payment.stripe.PaymentIntent.cancel",
                side_effect=stripe.APIConnectionError("down"),
            ) as cancel,
        ):
            result = auto_cancel_stuck_pending_orders()

        assert result["skipped_open"] == 1
        assert result["errors"] == 0
        cancel.assert_not_called()
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING

    def test_an_order_with_no_intent_asks_stripe_nothing(self):
        order = _unpaid(provider="stripe", payment_id="")
        _age(order, hold_expires=timezone.now() - timedelta(minutes=6))
        with (
            _provider_with_stripe_key(),
            patch(
                "order.payments.payment.stripe.PaymentIntent.cancel"
            ) as cancel,
        ):
            auto_cancel_stuck_pending_orders()

        cancel.assert_not_called()


class TestAFailedPaymentIsClosedBeforeTheOrderIsCanceled:
    """A failed attempt is not the end of the payment: the shopper may try
    another card on the same PaymentIntent or payment order."""

    @staticmethod
    def _failed(provider, **kwargs):
        order = _unpaid(
            provider=provider, payment_status=PaymentStatus.FAILED, **kwargs
        )
        Order.objects.filter(pk=order.pk).update(
            updated_at=timezone.now() - timedelta(minutes=31)
        )
        return order

    def test_the_intent_is_canceled_then_the_order(self):
        order = self._failed("stripe", payment_id="pi_declined")
        with (
            _provider_with_stripe_key(),
            patch(
                "order.payments.payment.stripe.PaymentIntent.retrieve",
                return_value=Mock(status="requires_payment_method"),
            ),
            patch(
                "order.payments.payment.stripe.PaymentIntent.cancel",
                return_value=Mock(status="canceled"),
            ) as cancel,
        ):
            result = auto_cancel_stuck_pending_orders()

        assert cancel.call_args.args == ("pi_declined",)
        assert result["canceled_failed"] == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED

    def test_an_intent_stripe_already_canceled_still_cancels_the_order(self):
        order = self._failed("stripe", payment_id="pi_gone")
        with (
            _provider_with_stripe_key(),
            patch(
                "order.payments.payment.stripe.PaymentIntent.cancel",
                side_effect=stripe.InvalidRequestError("canceled", "id"),
            ),
            patch(
                "order.payments.payment.stripe.PaymentIntent.retrieve",
                return_value=Mock(status="canceled"),
            ),
        ):
            result = auto_cancel_stuck_pending_orders()

        assert result["canceled_failed"] == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED

    def test_an_intent_that_succeeded_meanwhile_settles_the_order(self):
        """The shopper paid again before the sweep and its
        ``payment_intent.succeeded`` has not landed: the sweep settles the
        order through that handler instead of canceling it."""
        order = self._failed("stripe", payment_id="pi_paid_late")
        owed = order.calculate_order_total_amount()
        intent = stripe.PaymentIntent.construct_from(
            {
                "id": "pi_paid_late",
                "status": "succeeded",
                "amount_received": round(owed.amount * 100),
                "currency": str(owed.currency).lower(),
            },
            "sk_test_dummy",
        )
        with (
            _provider_with_stripe_key(),
            patch(
                "order.payments.payment.stripe.PaymentIntent.retrieve",
                return_value=intent,
            ),
            patch(
                "order.payments.payment.stripe.PaymentIntent.cancel"
            ) as cancel,
        ):
            result = auto_cancel_stuck_pending_orders()

        assert result["canceled_failed"] == 0
        assert result["settled"] == 1
        cancel.assert_not_called()
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED

    def test_a_provider_outage_leaves_the_order_for_the_next_sweep(self):
        order = self._failed("stripe", payment_id="pi_declined")
        with (
            _provider_with_stripe_key(),
            patch(
                "order.payments.payment.stripe.PaymentIntent.retrieve",
                side_effect=stripe.APIConnectionError("down"),
            ),
            patch(
                "order.payments.payment.stripe.PaymentIntent.cancel",
                side_effect=stripe.APIConnectionError("down"),
            ) as cancel,
        ):
            result = auto_cancel_stuck_pending_orders()

        assert result["skipped_open"] == 1
        assert result["errors"] == 0
        cancel.assert_not_called()
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING

    def test_a_stripe_intent_is_canceled_without_waiting_for_the_hold(self):
        """The hold is the wait of a payment that cannot be closed; one the
        provider cancels releases its stock at once."""
        order = self._failed("stripe", payment_id="pi_declined")
        _age(order, hold_expires=timezone.now() + timedelta(minutes=20))
        with (
            _provider_with_stripe_key(),
            patch(
                "order.payments.payment.stripe.PaymentIntent.retrieve",
                return_value=Mock(status="requires_payment_method"),
            ),
            patch(
                "order.payments.payment.stripe.PaymentIntent.cancel",
                return_value=Mock(status="canceled"),
            ),
        ):
            result = auto_cancel_stuck_pending_orders()

        assert result["canceled_failed"] == 1

    def test_a_payment_order_still_payable_keeps_the_order_open(self):
        """Viva keeps a payment order payable after a failed transaction
        until its own expiry, which nothing closes earlier: the order
        waits for the stored hold like a pending one."""
        order = self._failed("viva_wallet", payment_id="")
        _age(order, hold_expires=timezone.now() + timedelta(minutes=10))

        with _provider_with_viva_credentials():
            result = auto_cancel_stuck_pending_orders()

        assert result["canceled_failed"] == 0
        assert result["skipped_open"] == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING

    def test_a_payment_order_that_expired_cancels_the_order(self):
        order = self._failed("viva_wallet", payment_id="")
        _age(order, hold_expires=timezone.now() - timedelta(minutes=6))

        with _provider_with_viva_credentials():
            result = auto_cancel_stuck_pending_orders()

        assert result["canceled_failed"] == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED

    def test_an_unconfigured_provider_waits_for_the_hold_too(self):
        """Removed credentials cannot close the payment, and a Viva code
        stays payable until it expires: the same wait, not a cancel."""
        order = self._failed("viva_wallet", payment_id="")
        _age(order, hold_expires=timezone.now() + timedelta(minutes=10))

        result = auto_cancel_stuck_pending_orders()

        assert result["canceled_failed"] == 0
        assert result["skipped_open"] == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING

    def test_an_unconfigured_provider_cancels_once_the_hold_ended(self):
        order = self._failed("viva_wallet", payment_id="")
        _age(order, hold_expires=timezone.now() - timedelta(minutes=6))

        result = auto_cancel_stuck_pending_orders()

        assert result["canceled_failed"] == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED


class TestALatePaymentAfterTheHoldIsBookedSafely:
    def test_a_viva_charge_after_the_sweep_is_booked_for_refund(self):
        order = _unpaid(
            provider="viva_wallet", metadata={"viva_order_codes": ["OC9"]}
        )
        _age(order, hold_expires=timezone.now() - timedelta(minutes=6))
        # Viva is read before the sweep cancels, and lists no payment on OC9.
        with FakeVivaApi(
            by_order_code={"OC9": (200, {"Transactions": [], "Success": True})}
        ) as viva:
            auto_cancel_stuck_pending_orders()
        assert [r.url for r in viva.sent if "ordercode=OC9" in r.url]
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED

        with (
            patch(
                "order.payments.viva_settlement.verify_transaction",
                return_value=(
                    PaymentStatus.COMPLETED,
                    verified_payment(order, "OC9"),
                ),
            ),
            patch("emails.send.send_email") as alert,
            patch(
                "shipping.services.ShippingService.dispatch_create_shipment_task"
            ) as dispatch,
        ):
            outcome = handle_payment_created(
                order, {"StatusId": "F"}, "viva_txn_late"
            )

        assert outcome is None
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert order.payment_status == PaymentStatus.COMPLETED
        assert order.metadata["payment_after_cancel"]["payment_id"] == (
            "viva_txn_late"
        )
        alert.assert_called_once()
        dispatch.assert_not_called()


class TestAnUnpaidPileUpIsLogged:
    def test_logged_at_the_threshold(self, caplog):
        _set(ALERT, 2)
        for _ in range(2):
            _unpaid()

        with caplog.at_level("WARNING", logger="order.tasks"):
            auto_cancel_stuck_pending_orders()

        record = next(
            r for r in caplog.records if r.msg == payment_hold.SPIKE_MESSAGE
        )
        assert record.levelname == "WARNING"
        assert record.fields == {"unpaid_online_orders": 2, "threshold": 2}

    def test_an_order_whose_payment_failed_is_still_held(self, caplog):
        """A failed attempt keeps the stock until the sweep cancels the
        order, so it is part of the pile-up."""
        _set(ALERT, 2)
        _unpaid()
        _unpaid(payment_status=PaymentStatus.FAILED)

        with caplog.at_level("WARNING", logger="order.tasks"):
            auto_cancel_stuck_pending_orders()

        record = next(
            r for r in caplog.records if r.msg == payment_hold.SPIKE_MESSAGE
        )
        assert record.fields == {"unpaid_online_orders": 2, "threshold": 2}

    def test_silent_below_the_threshold(self, caplog):
        _set(ALERT, 3)
        for _ in range(2):
            _unpaid()

        with caplog.at_level("WARNING", logger="order.tasks"):
            auto_cancel_stuck_pending_orders()

        assert not [
            r for r in caplog.records if r.msg == payment_hold.SPIKE_MESSAGE
        ]

    def test_off_when_the_threshold_is_zero(self, caplog):
        for _ in range(3):
            _unpaid()

        with caplog.at_level("WARNING", logger="order.tasks"):
            auto_cancel_stuck_pending_orders()

        assert not [
            r for r in caplog.records if r.msg == payment_hold.SPIKE_MESSAGE
        ]

    def test_only_the_orders_still_held_count(self, caplog):
        """Orders the sweep just canceled are not a pile-up."""
        _set(ALERT, 2)
        for _ in range(2):
            order = _unpaid()
            _age(order, hold_expires=timezone.now() - timedelta(minutes=6))

        with caplog.at_level("WARNING", logger="order.tasks"):
            auto_cancel_stuck_pending_orders()

        assert not [
            r for r in caplog.records if r.msg == payment_hold.SPIKE_MESSAGE
        ]


class TestTheTotalHoldIsBounded:
    """A new payment link never moves the hold past created_at + max."""

    @staticmethod
    def _client(user):
        client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
        client.force_authenticate(user=user)
        return client

    @staticmethod
    def _post(client, order):
        return client.post(
            reverse("order-create-checkout-session", kwargs={"pk": order.pk}),
            {
                "successUrl": "https://shop.example/ok",
                "cancelUrl": "https://shop.example/no",
            },
            format="json",
        )

    @staticmethod
    def _providers(provider):
        return (
            patch(
                "pay_way.services.PayWayService.is_provider_configured",
                return_value=True,
            ),
            patch(
                "order.views.order.get_payment_provider",
                return_value=provider,
            ),
        )

    @staticmethod
    def _opening_provider(provider_code: str, expires_at):
        provider = Mock()
        provider.min_hosted_session = timedelta(0)
        provider.checkout_session_params.return_value = {}
        # No link of the order is payable yet, so one is opened (#643).
        provider.live_checkout_session.return_value = None
        provider.recorded_checkout_session.return_value = None
        provider.create_checkout_session.return_value = (
            True,
            {
                "session_id": "S2",
                "checkout_url": "https://pay.example/S2",
                "status": "created",
                "amount": "10.00",
                "currency": "EUR",
                "provider": provider_code,
                "expires_at": expires_at,
            },
        )
        return provider

    def test_chained_links_stop_at_the_limit(self):
        user = UserAccountFactory(num_addresses=0)
        order = _unpaid(user=user, provider="viva_wallet")
        created = timezone.now() - timedelta(minutes=50)
        _age(order, created=created)
        limit = created + timedelta(minutes=60)
        provider = self._opening_provider("viva_wallet", limit)

        first, second = self._providers(provider)
        with first, second:
            response = self._post(self._client(user), order)

        assert response.status_code == 200, response.data
        asked = provider.create_checkout_session.call_args.kwargs["expires_at"]
        assert asked == limit
        order.refresh_from_db()
        assert order.payment_hold_expires_at == limit

    def test_a_link_inside_the_window_gets_the_full_hold(self):
        order = _unpaid(provider="viva_wallet")
        _age(order, created=timezone.now() - timedelta(minutes=10))

        asked = payment_hold.deadline(order)

        assert (
            timedelta(minutes=29)
            < asked - timezone.now()
            <= timedelta(minutes=30)
        )

    def test_a_new_link_inside_the_window_works(self):
        user = UserAccountFactory(num_addresses=0)
        order = _unpaid(user=user, provider="stripe")
        _age(order, created=timezone.now() - timedelta(minutes=10))
        provider = self._opening_provider(
            "stripe", timezone.now() + timedelta(minutes=30)
        )

        first, second = self._providers(provider)
        with first, second:
            response = self._post(self._client(user), order)

        assert response.status_code == 200, response.data

    def test_a_new_link_past_the_window_is_refused(self):
        user = UserAccountFactory(num_addresses=0)
        order = _unpaid(user=user, provider="stripe")
        _age(order, created=timezone.now() - timedelta(minutes=61))
        provider = Mock()

        first, second = self._providers(provider)
        with first, second:
            response = self._post(self._client(user), order)

        assert response.status_code == 400
        assert response.json()["code"] == "payment_window_expired"
        provider.create_checkout_session.assert_not_called()

    def test_a_stripe_session_that_no_longer_fits_is_refused(self):
        """Under Stripe's 30 minute minimum a session would outlive the
        limit, so none is opened."""
        user = UserAccountFactory(num_addresses=0)
        order = _unpaid(user=user, provider="stripe")
        _age(order, created=timezone.now() - timedelta(minutes=40))
        provider = Mock()
        provider.min_hosted_session = StripePaymentProvider.min_hosted_session
        provider.checkout_session_params.return_value = {}
        # No link of the order is payable yet, so one is opened (#643).
        provider.live_checkout_session.return_value = None
        provider.recorded_checkout_session.return_value = None

        first, second = self._providers(provider)
        with first, second:
            response = self._post(self._client(user), order)

        assert response.status_code == 400
        assert response.json()["code"] == "payment_window_expired"
        provider.create_checkout_session.assert_not_called()

    def test_viva_gets_the_remaining_seconds(self):
        order = _unpaid(provider="viva_wallet")
        _age(order, created=timezone.now() - timedelta(minutes=50))
        provider = _viva_provider()
        response = Mock()
        response.json.return_value = {"orderCode": 123456}
        with (
            patch.object(provider, "_get_access_token", return_value="t"),
            patch("requests.post", return_value=response) as post,
        ):
            provider.create_checkout_session(
                Money(Decimal(10), settings.DEFAULT_CURRENCY),
                "7",
                order_uuid="u",
                expires_at=payment_hold.deadline(order),
            )

        assert 590 <= post.call_args.kwargs["json"]["paymentTimeout"] <= 600

    def test_the_cart_survives_the_cancel(self):
        """A shopper whose window ran out starts again from the same cart."""
        from cart.models import CartItem
        from tests.integration.order.test_unpaid_order_limit import (
            _place,
        )

        order = _place()
        _age(order, hold_expires=timezone.now() - timedelta(minutes=6))
        assert CartItem.objects.count() == 1

        auto_cancel_stuck_pending_orders()

        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert CartItem.objects.count() == 1

    def test_the_max_must_not_be_shorter_than_the_hold(self):
        _set(HOLD, 45)
        assert payment_hold.validate_max_hold_minutes_setting(45) is True
        assert payment_hold.validate_max_hold_minutes_setting(44) is False
        assert payment_hold.validate_max_hold_minutes_setting("x") is False


class TestTheHoldSettingIsValidated:
    @pytest.mark.parametrize(
        ("value", "ok"),
        [(29, False), (30, True), (1092, True), (1093, False), ("x", False)],
    )
    def test_bounds(self, value, ok):
        assert payment_hold.validate_hold_minutes_setting(value) is ok

    def test_the_retired_setting_is_gone(self):
        from django.conf import settings as django_settings

        names = {s["name"] for s in django_settings.EXTRA_SETTINGS_DEFAULTS}
        assert "ORDER_AUTO_CANCEL_PENDING_HOURS" not in names
        assert HOLD in names
