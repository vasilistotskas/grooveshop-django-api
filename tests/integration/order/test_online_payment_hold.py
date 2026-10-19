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

from order import payment_hold
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.models.order import Order
from order.payment import StripePaymentProvider, VivaWalletPaymentProvider
from order.payment_binding import PaymentPurpose
from order.stock import StockManager
from order.tasks import auto_cancel_stuck_pending_orders
from order.views.viva_webhook import _handle_payment_created
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from pay_way.services import PayWayService
from product.factories import ProductFactory
from tests.utils.viva import verified_payment
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


def _viva_provider():
    with patch(
        "tenant.credentials.viva_wallet_credentials",
        return_value={
            "merchant_id": "m",
            "api_key": "k",
            "client_id": "c",
            "client_secret": "s",
            "source_code": "Default",
            "live_mode": False,
        },
    ):
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
                "order.payment.stripe.checkout.Session.create",
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
                "order.payment.stripe.checkout.Session.create",
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
    """Hosted checkout, the PaymentIntent endpoint and retry-payment."""

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

    @pytest.mark.parametrize("provider_code", ["stripe", "viva_wallet"])
    def test_hosted_checkout_session(self, provider_code):
        user = UserAccountFactory(num_addresses=0)
        order = _unpaid(user=user, provider=provider_code)
        applied = timezone.now() + timedelta(minutes=31)
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
        provider.min_hosted_session = timedelta(0)

        with (
            self._configured(),
            patch(
                "order.views.order.get_payment_provider", return_value=provider
            ),
        ):
            response = self._client(user).post(
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
        asked = provider.create_checkout_session.call_args.kwargs["expires_at"]
        assert (
            timedelta(minutes=29)
            < asked - timezone.now()
            <= timedelta(minutes=30)
        )
        order.refresh_from_db()
        assert order.payment_hold_expires_at == applied

    @pytest.mark.parametrize(
        "route", ["order-create-payment-intent", "order-retry-payment"]
    )
    def test_payment_intent_routes(self, route):
        user = UserAccountFactory(num_addresses=0)
        order = _unpaid(
            user=user,
            provider="stripe",
            payment_status=(
                PaymentStatus.FAILED
                if route == "order-retry-payment"
                else PaymentStatus.PENDING
            ),
            payment_id="pi_old" if route == "order-retry-payment" else "",
        )
        provider = Mock()
        provider.process_payment.return_value = (
            True,
            {
                "payment_id": "pi_new",
                "status": PaymentStatus.PENDING,
                "client_secret": "secret",
                "amount": "10.00",
                "currency": "EUR",
                "provider": "stripe",
            },
        )

        with (
            self._configured(),
            patch(
                "pay_way.services.PayWayService.get_provider_for_pay_way",
                return_value=provider,
            ),
        ):
            response = self._client(user).post(
                reverse(route, kwargs={"pk": order.pk}), {}, format="json"
            )

        assert response.status_code == 200, response.data
        asked = provider.process_payment.call_args.kwargs["expires_at"]
        assert (
            timedelta(minutes=29)
            < asked - timezone.now()
            <= timedelta(minutes=30)
        )
        order.refresh_from_db()
        # A PaymentIntent has no expiry of its own: the deadline asked for
        # is the one stored, and the sweep cancels the intent at it.
        assert order.payment_hold_expires_at == asked

    def test_the_stored_expiry_is_the_one_the_provider_applied(self):
        order = _unpaid(provider="viva_wallet")
        applied = timezone.now() + timedelta(minutes=45)
        provider = Mock()
        provider.process_payment.return_value = (
            True,
            {"payment_id": "VC1", "expires_at": applied},
        )

        with patch(
            "pay_way.services.PayWayService.get_provider_for_pay_way",
            return_value=provider,
        ):
            PayWayService.process_payment(order.pay_way, order)

        order.refresh_from_db()
        assert order.payment_hold_expires_at == applied

    def test_the_per_store_hold_is_respected(self):
        _set(HOLD, 120)
        _set(MAX_HOLD, 240)
        order = _unpaid(provider="stripe")
        provider = Mock()
        provider.process_payment.return_value = (True, {"payment_id": "pi_1"})

        with patch(
            "pay_way.services.PayWayService.get_provider_for_pay_way",
            return_value=provider,
        ):
            PayWayService.process_payment(order.pay_way, order)

        asked = provider.process_payment.call_args.kwargs["expires_at"]
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

    def test_a_retry_that_extends_the_link_extends_the_hold(self):
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
                "order.payment.stripe.PaymentIntent.cancel",
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
                "order.payment.stripe.PaymentIntent.cancel",
                side_effect=stripe.InvalidRequestError("busy", "id"),
            ),
            patch(
                "order.payment.stripe.PaymentIntent.retrieve",
                return_value=Mock(status="succeeded"),
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
                "order.payment.stripe.PaymentIntent.cancel",
                side_effect=stripe.APIConnectionError("down"),
            ),
        ):
            result = auto_cancel_stuck_pending_orders()

        assert result["errors"] == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING

    def test_an_order_with_no_intent_asks_stripe_nothing(self):
        order = _unpaid(provider="stripe", payment_id="")
        _age(order, hold_expires=timezone.now() - timedelta(minutes=6))
        with (
            _provider_with_stripe_key(),
            patch("order.payment.stripe.PaymentIntent.cancel") as cancel,
        ):
            auto_cancel_stuck_pending_orders()

        cancel.assert_not_called()


class TestARetryClosesThePaymentItReplaces:
    """A PaymentIntent has no expiry, so the one a retry replaces would
    stay confirmable from an old tab after the new one was swept."""

    @staticmethod
    def _retry(order, *, cancel=None, retrieve=None):
        opened = (
            True,
            {
                "payment_id": "pi_new",
                "status": PaymentStatus.PENDING,
                "client_secret": "s",
            },
        )
        with (
            _provider_with_stripe_key(),
            patch(
                "order.payment.stripe.PaymentIntent.cancel", **(cancel or {})
            ) as cancelled,
            patch(
                "order.payment.stripe.PaymentIntent.retrieve",
                **(retrieve or {}),
            ),
            patch.object(
                StripePaymentProvider, "process_payment", return_value=opened
            ) as process,
        ):
            result = PayWayService.process_payment(order.pay_way, order)
        return result, cancelled, process

    def test_the_replaced_intent_is_canceled_first(self):
        order = _unpaid(
            provider="stripe",
            payment_id="pi_old",
            payment_status=PaymentStatus.FAILED,
        )

        (ok, _data), cancelled, process = self._retry(
            order, cancel={"return_value": Mock(status="canceled")}
        )

        assert ok
        assert cancelled.call_args.args == ("pi_old",)
        process.assert_called_once()
        order.refresh_from_db()
        assert order.payment_id == "pi_new"

    def test_a_payment_already_in_flight_refuses_the_retry(self):
        order = _unpaid(provider="stripe", payment_id="pi_busy")

        (ok, data), _cancelled, process = self._retry(
            order,
            cancel={"side_effect": stripe.InvalidRequestError("busy", "id")},
            retrieve={"return_value": Mock(status="processing")},
        )

        assert not ok
        assert data["code"] == "previous_payment_in_progress"
        process.assert_not_called()
        order.refresh_from_db()
        assert order.payment_id == "pi_busy"

    def test_a_provider_outage_refuses_the_retry(self):
        order = _unpaid(provider="stripe", payment_id="pi_old")

        (ok, data), _cancelled, process = self._retry(
            order, cancel={"side_effect": stripe.APIConnectionError("down")}
        )

        assert not ok
        assert data["code"] == "previous_payment_not_closed"
        process.assert_not_called()
        order.refresh_from_db()
        assert order.payment_id == "pi_old"

    def test_the_first_payment_has_nothing_to_close(self):
        order = _unpaid(provider="stripe", payment_id="")

        (ok, _data), cancelled, _process = self._retry(order)

        assert ok
        cancelled.assert_not_called()


class TestALatePaymentAfterTheHoldIsBookedSafely:
    def test_a_viva_charge_after_the_sweep_is_booked_for_refund(self):
        order = _unpaid(
            provider="viva_wallet", metadata={"viva_order_codes": ["OC9"]}
        )
        _age(order, hold_expires=timezone.now() - timedelta(minutes=6))
        auto_cancel_stuck_pending_orders()
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED

        with (
            patch(
                "order.views.viva_webhook._verify_transaction",
                return_value=(
                    PaymentStatus.COMPLETED,
                    verified_payment(order, "OC9"),
                ),
            ),
            patch("shipping.alerts.send_ops_alert") as alert,
            patch(
                "shipping.services.ShippingService.dispatch_create_shipment_task"
            ) as dispatch,
        ):
            outcome = _handle_payment_created(
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

    ROUTES = [
        "order-create-payment-intent",
        "order-retry-payment",
        "order-create-checkout-session",
    ]

    @staticmethod
    def _client(user):
        client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
        client.force_authenticate(user=user)
        return client

    @staticmethod
    def _post(client, route, order):
        body = {}
        if route == "order-create-checkout-session":
            body = {
                "successUrl": "https://shop.example/ok",
                "cancelUrl": "https://shop.example/no",
            }
        return client.post(
            reverse(route, kwargs={"pk": order.pk}), body, format="json"
        )

    @staticmethod
    def _providers(provider):
        return (
            patch(
                "pay_way.services.PayWayService.is_provider_configured",
                return_value=True,
            ),
            patch(
                "pay_way.services.PayWayService.get_provider_for_pay_way",
                return_value=provider,
            ),
            patch(
                "order.views.order.get_payment_provider",
                return_value=provider,
            ),
        )

    def test_chained_links_stop_at_the_limit(self):
        order = _unpaid(provider="viva_wallet")
        created = timezone.now() - timedelta(minutes=50)
        _age(order, created=created)
        provider = Mock()
        provider.process_payment.return_value = (True, {"payment_id": "VC1"})

        with patch(
            "pay_way.services.PayWayService.get_provider_for_pay_way",
            return_value=provider,
        ):
            PayWayService.process_payment(order.pay_way, order)

        asked = provider.process_payment.call_args.kwargs["expires_at"]
        assert asked == created + timedelta(minutes=60)
        order.refresh_from_db()
        assert order.payment_hold_expires_at == created + timedelta(minutes=60)

    def test_a_link_inside_the_window_gets_the_full_hold(self):
        order = _unpaid(provider="viva_wallet")
        _age(order, created=timezone.now() - timedelta(minutes=10))

        asked = payment_hold.deadline(order)

        assert (
            timedelta(minutes=29)
            < asked - timezone.now()
            <= timedelta(minutes=30)
        )

    @pytest.mark.parametrize("route", ROUTES)
    def test_a_retry_inside_the_window_works(self, route):
        user = UserAccountFactory(num_addresses=0)
        order = _unpaid(user=user, provider="stripe")
        _age(order, created=timezone.now() - timedelta(minutes=10))
        provider = Mock()
        provider.min_hosted_session = timedelta(0)
        provider.checkout_session_params.return_value = {}
        provider.create_checkout_session.return_value = (
            True,
            {
                "session_id": "S2",
                "checkout_url": "https://pay.example/S2",
                "status": "created",
                "amount": "10.00",
                "currency": "EUR",
                "provider": "stripe",
                "expires_at": timezone.now() + timedelta(minutes=30),
            },
        )
        provider.process_payment.return_value = (
            True,
            {
                "payment_id": "pi_2",
                "status": PaymentStatus.PENDING,
                "client_secret": "s",
                "amount": "10.00",
                "currency": "EUR",
                "provider": "stripe",
            },
        )

        first, second, third = self._providers(provider)
        with first, second, third:
            response = self._post(self._client(user), route, order)

        assert response.status_code == 200, response.data

    @pytest.mark.parametrize("route", ROUTES)
    def test_a_retry_past_the_window_is_refused(self, route):
        user = UserAccountFactory(num_addresses=0)
        order = _unpaid(user=user, provider="stripe")
        _age(order, created=timezone.now() - timedelta(minutes=61))
        provider = Mock()

        first, second, third = self._providers(provider)
        with first, second, third:
            response = self._post(self._client(user), route, order)

        assert response.status_code == 400
        assert response.json()["code"] == "payment_window_expired"
        provider.process_payment.assert_not_called()
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

        first, second, third = self._providers(provider)
        with first, second, third:
            response = self._post(
                self._client(user), "order-create-checkout-session", order
            )

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
