"""An order has at most one payment link a buyer can pay (#643).

Asking for a link is idempotent: while one of the order's links can be
paid, ``create_checkout_session`` returns it again and mints nothing. A
link that was paid while the order does not know it yet (its webhook has
not landed) stops every new link with 409 ``payment_in_progress``. A
provider that cannot say gets 503 ``payment_provider_unavailable``: a
link is never minted blind.

Viva is read with Retrieve order (``GET /api/orders/{orderCode}``, the
answers below are shaped as the demo environment answered on 2026-11-21);
Stripe with the Checkout Session's own ``status``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest
import requests
import stripe
from django.urls import reverse
from django.utils import timezone
from extra_settings.models import Setting
from rest_framework.test import APIClient

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models import Order
from order.payments import payment_hold
from order.payments.payment import VivaWalletPaymentProvider
from pay_way.factories import PayWayFactory
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

ATHENS = ZoneInfo("Europe/Athens")
# One instant in Greek winter time (EET, UTC+2) and one in summer time
# (EEST, UTC+3): a test that reads Viva's offset-less ExpirationDate runs
# at both, so its outcome does not depend on the day the suite runs.
SEASONS = {
    "EET": datetime(2027, 1, 15, 10, 0, tzinfo=UTC),
    "EEST": datetime(2027, 7, 15, 10, 0, tzinfo=UTC),
}


@pytest.fixture(params=sorted(SEASONS))
def frozen_now(request):
    """``timezone.now`` frozen at a winter or a summer instant."""
    instant = SEASONS[request.param]
    with patch("django.utils.timezone.now", return_value=instant):
        yield instant


@pytest.fixture(autouse=True)
def _windows():
    for name, value in (
        (payment_hold.HOLD_SETTING, 30),
        (payment_hold.GRACE_SETTING, 5),
        (payment_hold.MAX_HOLD_SETTING, 60),
    ):
        Setting.objects.update_or_create(
            name=name, defaults={"value_type": "int", "value_int": value}
        )


@pytest.fixture(autouse=True)
def _credentials():
    with (
        patch(
            "tenant.credentials.viva_wallet_credentials",
            return_value={
                "merchant_id": "m",
                "api_key": "k",
                "client_id": "c",
                "client_secret": "s",
                "source_code": "Default",
                "live_mode": False,
            },
        ),
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
        patch.object(
            VivaWalletPaymentProvider, "_get_access_token", return_value="t"
        ),
    ):
        yield


def _order(provider: str, **kwargs):
    user = UserAccountFactory(num_addresses=0)
    kwargs.setdefault("payment_status", PaymentStatus.PENDING)
    # No payment opened yet unless the test opens one.
    kwargs.setdefault("payment_id", "")
    order = OrderFactory(
        num_order_items=0,
        user=user,
        status=OrderStatus.PENDING,
        pay_way=PayWayFactory.create_online_payment(provider),
        **kwargs,
    )
    client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
    client.force_authenticate(user=user)
    return order, client


def _ask(order, client):
    return client.post(
        reverse("order-create-checkout-session", kwargs={"pk": order.pk}),
        {
            "successUrl": "https://shop.example/ok",
            "cancelUrl": "https://shop.example/no",
        },
        format="json",
    )


def _with_codes(order, *codes, hold_expires=None):
    order.metadata = {**(order.metadata or {}), "viva_order_codes": list(codes)}
    order.payment_hold_expires_at = hold_expires
    order.save(update_fields=["metadata", "payment_hold_expires_at"])


def _athens(instant: datetime) -> str:
    """``ExpirationDate`` as Viva writes it: Greek local time of the day
    it is written for, with no offset."""
    return (
        instant.astimezone(ATHENS)
        .replace(tzinfo=None)
        .isoformat(timespec="milliseconds")
    )


def _viva_order(code: str, state: int, expires: datetime) -> Mock:
    response = Mock(status_code=200)
    response.json.return_value = {
        "OrderCode": int(code),
        "StateId": state,
        "ExpirationDate": _athens(expires),
        "RequestAmount": 10.0,
    }
    response.raise_for_status.return_value = None
    return response


def _not_found() -> Mock:
    return Mock(status_code=404)


def _minted(code: str) -> Mock:
    response = Mock(status_code=200)
    response.json.return_value = {"orderCode": int(code)}
    response.raise_for_status.return_value = None
    return response


def _states(answers: dict[str, Mock]):
    """``requests.get`` answering Retrieve order per code."""

    def get(url, **_kwargs):
        return answers[url.rsplit("/", 1)[-1]]

    return patch("requests.get", side_effect=get)


class TestViva:
    def test_a_first_request_mints_a_link(self):
        order, client = _order("viva_wallet")

        with (
            _states({}),
            patch("requests.post", return_value=_minted("1111")) as post,
        ):
            response = _ask(order, client)

        assert response.status_code == 200, response.data
        assert response.data["session_id"] == "1111"
        post.assert_called_once()
        order.refresh_from_db()
        assert order.metadata["viva_order_codes"] == ["1111"]

    def test_a_pending_link_is_returned_again_with_its_own_expiry(
        self, frozen_now
    ):
        order, client = _order("viva_wallet")
        expires = frozen_now + timedelta(minutes=20)
        _with_codes(order, "1111", hold_expires=expires)

        with (
            _states({"1111": _viva_order("1111", 0, expires)}),
            patch("requests.post") as post,
        ):
            response = _ask(order, client)

        assert response.status_code == 200, response.data
        assert response.data["session_id"] == "1111"
        assert response.data["checkout_url"].endswith("ref=1111")
        assert response.data["status"] == "open"
        assert abs(response.data["expires_at"] - expires) < timedelta(seconds=1)
        post.assert_not_called()
        order.refresh_from_db()
        assert order.metadata["viva_order_codes"] == ["1111"]

    def test_a_paid_link_before_its_webhook_is_409(self, documented):
        order, client = _order("viva_wallet")
        expires = timezone.now() + timedelta(minutes=20)
        _with_codes(order, "1111", "2222", hold_expires=expires)

        with (
            _states(
                {
                    "2222": _viva_order("2222", 1, expires),
                    "1111": _viva_order("1111", 3, expires),
                }
            ),
            patch("requests.post") as post,
        ):
            response = _ask(order, client)

        assert response.status_code == 409, response.data
        documented(
            response, "/api/v1/order/{id}/create_checkout_session", "post"
        )
        assert response.data["code"] == "payment_in_progress"
        assert response["Retry-After"] == "10"
        post.assert_not_called()

    @pytest.mark.parametrize("state", [1, 2])
    def test_an_expired_or_canceled_link_is_replaced(self, state):
        order, client = _order("viva_wallet")
        ended = timezone.now() - timedelta(minutes=1)
        _with_codes(order, "1111", hold_expires=ended)

        with (
            _states({"1111": _viva_order("1111", state, ended)}),
            patch("requests.post", return_value=_minted("2222")) as post,
        ):
            response = _ask(order, client)

        assert response.status_code == 200, response.data
        assert response.data["session_id"] == "2222"
        post.assert_called_once()
        order.refresh_from_db()
        assert order.metadata["viva_order_codes"] == ["1111", "2222"]

    def test_a_forgotten_code_past_its_hold_is_gone(self):
        order, client = _order("viva_wallet")
        _with_codes(
            order,
            "1111",
            hold_expires=timezone.now() - timedelta(minutes=10),
        )

        with (
            _states({"1111": _not_found()}),
            patch("requests.post", return_value=_minted("2222")) as post,
        ):
            response = _ask(order, client)

        assert response.status_code == 200, response.data
        post.assert_called_once()

    @pytest.mark.parametrize(
        "ended_ago",
        [timedelta(minutes=-10), timedelta(minutes=2)],
        ids=["still-held", "in-the-grace-window"],
    )
    def test_a_404_for_a_code_that_may_be_payable_mints_nothing(
        self, ended_ago, documented
    ):
        order, client = _order("viva_wallet")
        _with_codes(order, "1111", hold_expires=timezone.now() - ended_ago)

        with (
            _states({"1111": _not_found()}),
            patch("requests.post") as post,
        ):
            response = _ask(order, client)

        assert response.status_code == 503, response.data
        documented(
            response, "/api/v1/order/{id}/create_checkout_session", "post"
        )
        assert response.data["code"] == "payment_provider_unavailable"
        assert response["Retry-After"] == "30"
        post.assert_not_called()

    @pytest.mark.parametrize(
        "failure",
        [
            requests.ConnectionError("down"),
            Mock(
                status_code=502,
                raise_for_status=Mock(side_effect=requests.HTTPError("502")),
            ),
        ],
        ids=["unreachable", "5xx"],
    )
    def test_a_provider_that_cannot_answer_mints_nothing(self, failure):
        order, client = _order("viva_wallet")
        _with_codes(
            order, "1111", hold_expires=timezone.now() + timedelta(minutes=5)
        )
        get = (
            patch("requests.get", side_effect=failure)
            if isinstance(failure, Exception)
            else patch("requests.get", return_value=failure)
        )

        with get, patch("requests.post") as post:
            response = _ask(order, client)

        assert response.status_code == 503, response.data
        post.assert_not_called()

    def test_a_concurrent_request_gets_the_link_that_was_recorded(
        self, frozen_now
    ):
        """Two requests read no live link and both mint; the second to
        reach the row lock answers with the first one's link and records
        nothing of its own, so the order still has one payable link."""
        order, client = _order("viva_wallet")
        expires = frozen_now + timedelta(minutes=30)

        def mint_while_another_request_records(*_args, **_kwargs):
            # The concurrent request commits its code between this
            # request's read and its own lock.
            Order.objects.filter(pk=order.pk).update(
                metadata={"viva_order_codes": ["1111"]},
                payment_hold_expires_at=expires,
            )
            return _minted("2222")

        answers = {"1111": _viva_order("1111", 0, expires)}
        with (
            _states(answers),
            patch(
                "requests.post", side_effect=mint_while_another_request_records
            ),
        ):
            response = _ask(order, client)

        assert response.status_code == 200, response.data
        assert response.data["session_id"] == "1111"
        order.refresh_from_db()
        assert order.metadata["viva_order_codes"] == ["1111"]
        assert order.payment_hold_expires_at == expires


class TestVivaExpirationDate:
    def test_the_repeated_autumn_hour_is_its_later_instant(self):
        """03:30 on the last Sunday of October happens twice in Athens;
        the later instant (+02:00) never takes a payable code for expired
        early."""
        provider = VivaWalletPaymentProvider()

        parsed = provider._expiration({"ExpirationDate": "2026-10-25T03:30:00"})

        assert parsed == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)

    @pytest.mark.parametrize(
        ("written", "utc"),
        [
            # EET, UTC+2 (the demo reading of 2026-11-20).
            ("2026-11-20T22:36:55.457", "2026-11-20T20:36:55.457+00:00"),
            # EEST, UTC+3: a fixed offset would be an hour off.
            ("2026-07-01T12:00:00.000", "2026-07-01T09:00:00+00:00"),
        ],
    )
    def test_is_greek_local_time(self, written, utc):
        provider = VivaWalletPaymentProvider()

        parsed = provider._expiration({"ExpirationDate": written})

        assert parsed == datetime.fromisoformat(utc)


def _stripe_session(
    session_id: str,
    status: str,
    expires: datetime,
    *,
    payment_status: str = "unpaid",
    intent_status: str | None = None,
):
    return SimpleNamespace(
        id=session_id,
        status=status,
        url=f"https://checkout.stripe.com/c/pay/{session_id}",
        expires_at=int(expires.timestamp()),
        payment_status=payment_status,
        payment_intent=(
            SimpleNamespace(status=intent_status) if intent_status else None
        ),
    )


def _with_session(order, session_id: str, expires: datetime) -> None:
    order.metadata = {"stripe_checkout_session_id": session_id}
    order.payment_hold_expires_at = expires
    order.save(update_fields=["metadata", "payment_hold_expires_at"])


class TestStripe:
    def test_an_open_session_is_returned_again(self):
        order, client = _order("stripe")
        expires = timezone.now() + timedelta(minutes=31)
        _with_session(order, "cs_1", expires)

        with (
            patch.object(
                stripe.checkout.Session,
                "retrieve",
                return_value=_stripe_session("cs_1", "open", expires),
            ),
            patch.object(stripe.checkout.Session, "create") as create,
        ):
            response = _ask(order, client)

        assert response.status_code == 200, response.data
        assert response.data["session_id"] == "cs_1"
        create.assert_not_called()

    def test_a_complete_session_of_an_unpaid_order_is_409(self):
        order, client = _order("stripe")
        expires = timezone.now() + timedelta(minutes=31)
        _with_session(order, "cs_1", expires)

        with (
            patch.object(
                stripe.checkout.Session,
                "retrieve",
                return_value=_stripe_session(
                    "cs_1", "complete", expires, payment_status="paid"
                ),
            ),
            patch.object(stripe.checkout.Session, "create") as create,
        ):
            response = _ask(order, client)

        assert response.status_code == 409, response.data
        assert response.data["code"] == "payment_in_progress"
        create.assert_not_called()

    def test_an_expired_session_is_replaced(self):
        order, client = _order("stripe")
        ended = timezone.now() - timedelta(minutes=1)
        _with_session(order, "cs_1", ended)
        new_expiry = timezone.now() + timedelta(minutes=31)

        with (
            patch.object(
                stripe.checkout.Session,
                "retrieve",
                return_value=_stripe_session("cs_1", "expired", ended),
            ),
            patch.object(
                stripe.checkout.Session,
                "create",
                return_value=_stripe_session("cs_2", "open", new_expiry),
            ) as create,
        ):
            response = _ask(order, client)

        assert response.status_code == 200, response.data
        assert response.data["session_id"] == "cs_2"
        create.assert_called_once()
        order.refresh_from_db()
        assert order.metadata["stripe_checkout_session_id"] == "cs_2"

    def test_an_intent_that_may_still_land_stops_a_session(self):
        order, client = _order("stripe", payment_id="pi_1")

        with (
            patch(
                "order.payments.payment.StripePaymentProvider.close_payment",
                return_value=False,
            ),
            patch.object(stripe.checkout.Session, "create") as create,
        ):
            response = _ask(order, client)

        assert response.status_code == 409, response.data
        assert response.data["code"] == "payment_in_progress"
        create.assert_not_called()

    def test_an_open_intent_is_closed_before_a_session_opens(self):
        order, client = _order("stripe", payment_id="pi_1")
        new_expiry = timezone.now() + timedelta(minutes=31)

        with (
            patch(
                "order.payments.payment.StripePaymentProvider.close_payment",
                return_value=True,
            ) as close,
            patch.object(
                stripe.checkout.Session,
                "create",
                return_value=_stripe_session("cs_2", "open", new_expiry),
            ),
        ):
            response = _ask(order, client)

        assert response.status_code == 200, response.data
        close.assert_called_once()

    def test_a_retry_expires_the_open_session_first(self):
        order, client = _order("stripe", payment_status=PaymentStatus.FAILED)
        expires = timezone.now() + timedelta(minutes=31)
        _with_session(order, "cs_1", expires)
        with (
            patch.object(
                stripe.checkout.Session,
                "retrieve",
                return_value=_stripe_session("cs_1", "open", expires),
            ),
            patch.object(stripe.checkout.Session, "expire") as expire,
            patch(
                "order.payments.payment.StripePaymentProvider.process_payment",
                return_value=(
                    True,
                    {
                        "payment_id": "pi_2",
                        "status": PaymentStatus.PENDING,
                        "client_secret": "secret",
                        "amount": "10.00",
                        "currency": "EUR",
                        "provider": "stripe",
                    },
                ),
            ),
        ):
            response = client.post(
                reverse("order-retry-payment", kwargs={"pk": order.pk}),
                {},
                format="json",
            )

        assert response.status_code == 200, response.data
        expire.assert_called_once_with("cs_1", api_key="sk_test_dummy")

    def test_a_retry_after_a_paid_session_is_refused(self):
        order, client = _order("stripe", payment_status=PaymentStatus.FAILED)
        expires = timezone.now() + timedelta(minutes=31)
        _with_session(order, "cs_1", expires)

        with (
            patch.object(
                stripe.checkout.Session,
                "retrieve",
                return_value=_stripe_session(
                    "cs_1", "complete", expires, payment_status="paid"
                ),
            ),
            patch.object(stripe.PaymentIntent, "create") as create,
        ):
            response = client.post(
                reverse("order-retry-payment", kwargs={"pk": order.pk}),
                {},
                format="json",
            )

        assert response.status_code == 400, response.data
        create.assert_not_called()


class TestStripeAfterThisReview:
    """The stand-in review of #768: a delayed payment that failed, a late
    ``checkout.session.expired`` event, and an intent and a session opened
    by concurrent requests from different endpoints."""

    @pytest.mark.parametrize(
        "intent_status", ["requires_payment_method", "canceled"]
    )
    def test_a_session_whose_delayed_payment_failed_is_replaced(
        self, intent_status
    ):
        order, client = _order("stripe")
        expires = timezone.now() + timedelta(minutes=31)
        _with_session(order, "cs_1", expires)

        with (
            patch.object(
                stripe.checkout.Session,
                "retrieve",
                return_value=_stripe_session(
                    "cs_1", "complete", expires, intent_status=intent_status
                ),
            ),
            patch.object(
                stripe.checkout.Session,
                "create",
                return_value=_stripe_session("cs_2", "open", expires),
            ) as create,
        ):
            response = _ask(order, client)

        assert response.status_code == 200, response.data
        assert response.data["session_id"] == "cs_2"
        create.assert_called_once()

    def test_a_session_whose_delayed_payment_is_processing_is_409(self):
        order, client = _order("stripe")
        expires = timezone.now() + timedelta(minutes=31)
        _with_session(order, "cs_1", expires)

        with (
            patch.object(
                stripe.checkout.Session,
                "retrieve",
                return_value=_stripe_session(
                    "cs_1", "complete", expires, intent_status="processing"
                ),
            ),
            patch.object(stripe.checkout.Session, "create") as create,
        ):
            response = _ask(order, client)

        assert response.status_code == 409, response.data
        assert response.data["code"] == "payment_in_progress"
        create.assert_not_called()

    def test_a_late_expired_event_keeps_the_recorded_session(self):
        from unittest.mock import Mock

        from djstripe.models import Event

        from order.signals.handlers import handle_stripe_checkout_expired

        order, client = _order("stripe")
        expires = timezone.now() + timedelta(minutes=31)
        _with_session(order, "cs_live", expires)
        event = Mock(spec=Event)
        event.id = "evt_expired_old"
        event.data = {
            "object": {"id": "cs_old", "metadata": {"order_id": str(order.id)}}
        }

        handle_stripe_checkout_expired(sender=None, event=event)

        order.refresh_from_db()
        assert order.metadata["stripe_checkout_session_id"] == "cs_live"
        with (
            patch.object(
                stripe.checkout.Session,
                "retrieve",
                return_value=_stripe_session("cs_live", "open", expires),
            ) as retrieve,
            patch.object(stripe.checkout.Session, "create") as create,
        ):
            response = _ask(order, client)

        assert response.status_code == 200, response.data
        assert response.data["session_id"] == "cs_live"
        assert retrieve.call_args.args[0] == "cs_live"
        create.assert_not_called()

    def test_a_session_loses_to_an_intent_opened_meanwhile(self):
        """A retry in one tab opens an intent while "pay now" in another
        mints a session: the session records nothing and is expired."""
        order, client = _order("stripe")
        expires = timezone.now() + timedelta(minutes=31)

        def mint_while_an_intent_is_recorded(**_kwargs):
            Order.objects.filter(pk=order.pk).update(payment_id="pi_2")
            return _stripe_session("cs_2", "open", expires)

        with (
            patch.object(
                stripe.checkout.Session,
                "create",
                side_effect=mint_while_an_intent_is_recorded,
            ),
            patch.object(stripe.checkout.Session, "expire") as expire,
        ):
            response = _ask(order, client)

        assert response.status_code == 409, response.data
        assert response.data["code"] == "order_changed_during_payment"
        expire.assert_called_once_with("cs_2", api_key="sk_test_dummy")
        order.refresh_from_db()
        assert "stripe_checkout_session_id" not in (order.metadata or {})
        assert order.payment_id == "pi_2"

    def test_an_intent_loses_to_a_session_opened_meanwhile(self):
        order, client = _order("stripe", payment_status=PaymentStatus.FAILED)

        def mint_while_a_session_is_recorded(*_args, **_kwargs):
            Order.objects.filter(pk=order.pk).update(
                metadata={"stripe_checkout_session_id": "cs_1"}
            )
            return (
                True,
                {
                    "payment_id": "pi_2",
                    "status": PaymentStatus.PENDING,
                    "client_secret": "secret",
                    "amount": "10.00",
                    "currency": "EUR",
                    "provider": "stripe",
                },
            )

        with patch(
            "order.payments.payment.StripePaymentProvider.process_payment",
            side_effect=mint_while_a_session_is_recorded,
        ):
            response = client.post(
                reverse("order-retry-payment", kwargs={"pk": order.pk}),
                {},
                format="json",
            )

        assert response.status_code == 409, response.data
        assert response.data["code"] == "order_changed_during_payment"
        order.refresh_from_db()
        assert order.payment_id == ""
        assert "client_secret" not in response.data

    def test_reusing_a_session_closes_an_open_intent(self):
        order, client = _order("stripe", payment_id="pi_1")
        expires = timezone.now() + timedelta(minutes=31)
        _with_session(order, "cs_1", expires)

        with (
            patch(
                "order.payments.payment.StripePaymentProvider.close_payment",
                return_value=True,
            ) as close,
            patch.object(
                stripe.checkout.Session,
                "retrieve",
                return_value=_stripe_session("cs_1", "open", expires),
            ),
        ):
            response = _ask(order, client)

        assert response.status_code == 200, response.data
        assert response.data["session_id"] == "cs_1"
        close.assert_called_once()
