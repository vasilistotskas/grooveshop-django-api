"""A payment the provider took is settled even when its webhook never lands.

The provider's webhook is what marks an online order paid. When it is lost
(an outage, a misconfigured endpoint, a local stack it cannot reach), the
order stays PENDING while the money has moved, and
``auto_cancel_stuck_pending_orders`` used to cancel it with
``refund_payment=False`` once its payment hold ended: a charged customer, a
canceled order, the stock released and nobody told.

Before the sweep cancels an order, and when a Viva buyer comes back from the
hosted page, the provider is read. A payment it took is settled through the
same code its webhook runs, so the webhook that arrives later changes
nothing. A provider that cannot be read never lets the sweep cancel.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
import stripe
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from djstripe.models import Event
from extra_settings.models import Setting

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.models.order import Order
from order.models.payment_transaction import PaymentTransaction
from order.models.viva_webhook_event import VivaWebhookEvent
from order.payments import payment_hold
from order.signals.handlers import (
    handle_stripe_checkout_completed,
    handle_stripe_payment_succeeded,
)
from order.tasks import (
    auto_cancel_stuck_pending_orders,
    send_order_confirmation_email,
)
from pay_way.factories import PayWayFactory
from tenant.models import Tenant
from tests.utils.viva import FakeVivaApi, retrieve_transaction

pytestmark = [pytest.mark.django_db]

CODE = "4507133103272600"
TXN = "e32827bb-03bf-43d1-abe9-593eb5d2cd43"
INTENT = "pi_landed_without_webhook"
SESSION = "cs_landed_without_webhook"

#: A listing row with no ``Amount`` key at all.
_ABSENT = object()


@pytest.fixture(autouse=True)
def _windows():
    """30 minutes' hold and 5 minutes' grace, as shipped."""
    for name, value in (
        (payment_hold.HOLD_SETTING, 30),
        (payment_hold.GRACE_SETTING, 5),
        (payment_hold.MAX_HOLD_SETTING, 60),
        (payment_hold.ALERT_SETTING, 0),
    ):
        Setting.objects.update_or_create(
            name=name, defaults={"value_type": "int", "value_int": value}
        )


@pytest.fixture
def confirmations():
    """Every order confirmation queued, once its transaction commits."""
    with patch.object(send_order_confirmation_email, "apply_async") as sent:
        yield sent


@pytest.fixture(autouse=True)
def _no_courier():
    with patch(
        "shipping.services.ShippingService.dispatch_create_shipment_task"
    ):
        yield


def _expired_unpaid(provider: str, **kwargs) -> Order:
    created = OrderFactory(
        num_order_items=1,
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        pay_way=PayWayFactory.create_online_payment(provider),
        **kwargs,
    )
    Order.objects.filter(pk=created.pk).update(
        payment_hold_expires_at=timezone.now() - timedelta(minutes=6)
    )
    return Order.objects.get(pk=created.pk)


def _viva_order() -> Order:
    return _expired_unpaid("viva_wallet", metadata={"viva_order_codes": [CODE]})


def _paid_at_viva(order: Order, **listing) -> FakeVivaApi:
    """Viva as it answers for a payment order the buyer paid: its
    transactions list the charge, and Retrieve Transaction confirms it."""
    owed = float(order.calculate_order_total_amount().amount)
    return FakeVivaApi(
        by_order_code={
            CODE: listing.get(
                "answer",
                (
                    200,
                    {
                        "Transactions": [
                            {
                                "TransactionId": TXN,
                                "StatusId": "F",
                                "Amount": owed,
                                "ParentId": None,
                                "CurrencyCode": "978",
                                "TransactionType": {
                                    "Name": "CardCharge",
                                    "TransactionTypeId": 5,
                                },
                            }
                        ],
                        "ErrorCode": 0,
                        "Success": True,
                    },
                ),
            )
        },
        transactions={
            TXN: (
                200,
                retrieve_transaction(
                    orderCode=int(CODE),
                    amount=owed,
                    statusId="F",
                    currencyCode="978",
                ),
            )
        },
    )


def _assert_settled_once(order: Order, reference: str) -> None:
    order.refresh_from_db()
    assert order.status == OrderStatus.PROCESSING
    assert order.payment_status == PaymentStatus.COMPLETED
    assert order.payment_id == reference
    assert (
        PaymentTransaction.objects.filter(
            order=order, provider_reference=reference
        ).count()
        == 1
    )


class TestVivaLostWebhook:
    def test_the_sweep_settles_a_paid_order_instead_of_canceling_it(
        self, django_capture_on_commit_callbacks, confirmations
    ):
        order = _viva_order()

        with (
            _paid_at_viva(order),
            django_capture_on_commit_callbacks(execute=True),
        ):
            result = auto_cancel_stuck_pending_orders()

        assert result["canceled_pending"] == 0
        _assert_settled_once(order, TXN)
        assert VivaWebhookEvent.objects.filter(
            transaction_id=TXN, event_type_id=1796
        ).exists()
        assert confirmations.call_count == 1

    def test_the_return_page_settles_a_paid_order(
        self, django_capture_on_commit_callbacks, confirmations
    ):
        order = _viva_order()

        with (
            _paid_at_viva(order),
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = Client().get(
                reverse("order-viva-return"), {"t": TXN, "s": CODE}
            )

        assert response.status_code == 200
        assert response.json()["paymentStatus"] == PaymentStatus.COMPLETED
        _assert_settled_once(order, TXN)
        assert confirmations.call_count == 1

    def test_the_webhook_after_the_settle_changes_nothing(
        self, django_capture_on_commit_callbacks, confirmations, monkeypatch
    ):
        order = _viva_order()
        with (
            _paid_at_viva(order),
            django_capture_on_commit_callbacks(execute=True),
        ):
            auto_cancel_stuck_pending_orders()

        tenant = Tenant(
            schema_name="viva_late_webhook",
            name="viva-late-webhook",
            slug="viva-late-webhook",
            owner_email="owner-viva-late-webhook@example.com",
            is_active=True,
            suspended_at=None,
        )
        tenant.auto_create_schema = False
        tenant.save()

        @contextmanager
        def _here(_tenant):
            yield

        monkeypatch.setattr("order.views.viva_webhook.tenant_context", _here)
        with (
            _paid_at_viva(order),
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = Client().post(
                reverse("viva-wallet-webhook"),
                data=json.dumps(
                    {
                        "EventTypeId": 1796,
                        "EventData": {
                            "TransactionId": TXN,
                            "OrderCode": CODE,
                            "StatusId": "F",
                        },
                    }
                ),
                content_type="application/json",
            )

        assert response.status_code == 200
        _assert_settled_once(order, TXN)
        assert (
            VivaWebhookEvent.objects.filter(
                transaction_id=TXN, event_type_id=1796
            ).count()
            == 1
        )
        assert confirmations.call_count == 1

    def test_viva_unreadable_at_sweep_time_cancels_nothing(self):
        order = _viva_order()

        with _paid_at_viva(order, answer=(503, "Service Unavailable")):
            result = auto_cancel_stuck_pending_orders()

        assert result["canceled_pending"] == 0
        assert result["skipped_open"] == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING
        assert order.payment_status == PaymentStatus.PENDING

    def test_a_payment_order_nobody_paid_is_still_canceled(self):
        order = _viva_order()

        with _paid_at_viva(
            order, answer=(200, {"Transactions": [], "Success": True})
        ):
            result = auto_cancel_stuck_pending_orders()

        assert result["canceled_pending"] == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED


def _stripe_credentials():
    return patch(
        "tenant.credentials.stripe_credentials",
        return_value={
            "secret_key": "sk_test_dummy",
            "publishable_key": "pk_test_dummy",
            "live_mode": False,
        },
    )


def _minor(order: Order) -> int:
    return round(order.calculate_order_total_amount().amount * 100)


class TestStripeLostWebhook:
    def _session(self, order: Order, **overrides):
        return stripe.checkout.Session.construct_from(
            {
                "id": SESSION,
                "object": "checkout.session",
                "status": "complete",
                "payment_status": "paid",
                "payment_intent": INTENT,
                "amount_total": _minor(order),
                "currency": "eur",
                "metadata": {"order_id": str(order.id)},
                **overrides,
            },
            "sk_test_dummy",
        )

    def _intent(self, order: Order, status: str = "succeeded"):
        return stripe.PaymentIntent.construct_from(
            {
                "id": INTENT,
                "object": "payment_intent",
                "status": status,
                "amount": _minor(order),
                "amount_received": _minor(order),
                "currency": "eur",
                "latest_charge": None,
                "metadata": {},
            },
            "sk_test_dummy",
        )

    def test_the_sweep_settles_a_paid_checkout_session(
        self, django_capture_on_commit_callbacks, confirmations
    ):
        order = _expired_unpaid(
            "stripe", metadata={"stripe_checkout_session_id": SESSION}
        )

        with (
            _stripe_credentials(),
            patch(
                "stripe.checkout.Session.retrieve",
                return_value=self._session(order),
            ),
            django_capture_on_commit_callbacks(execute=True),
        ):
            result = auto_cancel_stuck_pending_orders()

        assert result["canceled_pending"] == 0
        _assert_settled_once(order, INTENT)
        assert confirmations.call_count == 1

    def test_a_paid_session_behind_a_canceled_intent_is_settled(
        self, django_capture_on_commit_callbacks, confirmations
    ):
        """Opening a hosted link cancels the order's open PaymentIntent and
        leaves its ``payment_id``: the session recorded after it is still
        read, and paid, it settles the order."""
        order = _expired_unpaid(
            "stripe",
            payment_id="pi_closed_for_the_link",
            metadata={"stripe_checkout_session_id": SESSION},
        )

        with (
            _stripe_credentials(),
            patch(
                "stripe.PaymentIntent.retrieve",
                return_value=self._intent(order, status="canceled"),
            ),
            patch(
                "stripe.checkout.Session.retrieve",
                return_value=self._session(order),
            ),
            patch("stripe.PaymentIntent.cancel") as cancel,
            django_capture_on_commit_callbacks(execute=True),
        ):
            result = auto_cancel_stuck_pending_orders()

        assert result["settled"] == 1
        assert result["canceled_pending"] == 0
        cancel.assert_not_called()
        _assert_settled_once(order, INTENT)
        assert confirmations.call_count == 1

    def test_a_payment_opened_during_the_sweep_is_not_canceled(self):
        """The sweep reads the provider and closes the payment before it
        locks the order. A link recorded meanwhile is a payment the read
        did not see: the order is left for the next sweep."""
        order = _expired_unpaid("stripe", payment_id=INTENT)

        def _closed_while_a_link_opens(*_args, **_kwargs):
            Order.objects.filter(pk=order.pk).update(
                metadata={"stripe_checkout_session_id": "cs_opened_meanwhile"}
            )
            return self._intent(order, status="canceled")

        with (
            _stripe_credentials(),
            patch(
                "stripe.PaymentIntent.retrieve",
                return_value=self._intent(
                    order, status="requires_payment_method"
                ),
            ),
            patch(
                "stripe.PaymentIntent.cancel",
                side_effect=_closed_while_a_link_opens,
            ),
        ):
            result = auto_cancel_stuck_pending_orders()

        assert result["canceled_pending"] == 0
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING

    def test_the_session_webhook_after_the_settle_changes_nothing(
        self, django_capture_on_commit_callbacks, confirmations
    ):
        order = _expired_unpaid(
            "stripe", metadata={"stripe_checkout_session_id": SESSION}
        )
        with (
            _stripe_credentials(),
            patch(
                "stripe.checkout.Session.retrieve",
                return_value=self._session(order),
            ),
            django_capture_on_commit_callbacks(execute=True),
        ):
            auto_cancel_stuck_pending_orders()

        event = Mock(spec=Event)
        event.id = "evt_late_session"
        event.type = "checkout.session.completed"
        event.data = {"object": dict(self._session(order))}
        with django_capture_on_commit_callbacks(execute=True):
            handle_stripe_checkout_completed(sender=None, event=event)

        _assert_settled_once(order, INTENT)
        assert confirmations.call_count == 1

    def test_the_sweep_settles_a_succeeded_payment_intent(
        self, django_capture_on_commit_callbacks, confirmations
    ):
        order = _expired_unpaid("stripe", payment_id=INTENT)

        with (
            _stripe_credentials(),
            patch(
                "stripe.PaymentIntent.cancel",
                side_effect=stripe.InvalidRequestError(
                    "This PaymentIntent's status is succeeded", None
                ),
            ),
            patch(
                "stripe.PaymentIntent.retrieve",
                return_value=self._intent(order),
            ),
            django_capture_on_commit_callbacks(execute=True),
        ):
            result = auto_cancel_stuck_pending_orders()

        assert result["canceled_pending"] == 0
        _assert_settled_once(order, INTENT)
        assert confirmations.call_count == 1

    def test_the_intent_webhook_after_the_settle_changes_nothing(
        self, django_capture_on_commit_callbacks, confirmations
    ):
        order = _expired_unpaid("stripe", payment_id=INTENT)
        with (
            _stripe_credentials(),
            patch(
                "stripe.PaymentIntent.cancel",
                side_effect=stripe.InvalidRequestError("succeeded", None),
            ),
            patch(
                "stripe.PaymentIntent.retrieve",
                return_value=self._intent(order),
            ),
            django_capture_on_commit_callbacks(execute=True),
        ):
            auto_cancel_stuck_pending_orders()

        event = Mock(spec=Event)
        event.id = "evt_late_intent"
        event.type = "payment_intent.succeeded"
        event.data = {"object": dict(self._intent(order))}
        with (
            _stripe_credentials(),
            django_capture_on_commit_callbacks(execute=True),
        ):
            handle_stripe_payment_succeeded(sender=None, event=event)

        _assert_settled_once(order, INTENT)
        assert confirmations.call_count == 1

    def test_stripe_unreadable_at_sweep_time_cancels_nothing(self):
        order = _expired_unpaid(
            "stripe", metadata={"stripe_checkout_session_id": SESSION}
        )

        with (
            _stripe_credentials(),
            patch(
                "stripe.checkout.Session.retrieve",
                side_effect=stripe.APIConnectionError("down"),
            ),
        ):
            result = auto_cancel_stuck_pending_orders()

        assert result["canceled_pending"] == 0
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING


class TestAPaymentThatSettlesNothingIsNotCountedSettled:
    def test_a_viva_charge_refused_without_a_flag_keeps_the_order_open(
        self, caplog
    ):
        """A charge the checks refuse without flagging it (Retrieve
        Transaction names another payment order) settled nothing: the
        sweep keeps the order for a person instead of calling it settled
        for ever."""
        order = _viva_order()
        api = _paid_at_viva(order)
        api.transactions[TXN] = (
            200,
            retrieve_transaction(
                orderCode=9999999999999999,
                amount=float(order.calculate_order_total_amount().amount),
                statusId="F",
                currencyCode="978",
            ),
        )

        with api, caplog.at_level("ERROR", logger="order.payments"):
            result = auto_cancel_stuck_pending_orders()

        assert result["settled"] == 0
        assert result["skipped_open"] == 1
        assert result["canceled_pending"] == 0
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING
        assert any(r.levelname == "ERROR" for r in caplog.records)

    def test_a_stripe_session_still_being_paid_is_not_settled(self):
        order = _expired_unpaid(
            "stripe", metadata={"stripe_checkout_session_id": SESSION}
        )
        session = TestStripeLostWebhook()._session(
            order, payment_status="unpaid", payment_intent=None
        )

        with (
            _stripe_credentials(),
            patch("stripe.checkout.Session.retrieve", return_value=session),
        ):
            result = auto_cancel_stuck_pending_orders()

        assert result["settled"] == 0
        assert result["skipped_open"] == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING


class TestAnUnreadableAmountIsAnUnreadableViva:
    def test_the_return_page_answers_with_the_order_as_it_is(self):
        order = _viva_order()
        api = _paid_at_viva(order)
        api.by_order_code[CODE][1]["Transactions"][0]["Amount"] = "n/a"

        with api:
            response = Client().get(
                reverse("order-viva-return"), {"t": TXN, "s": CODE}
            )

        assert response.status_code == 200
        assert response.json()["paymentStatus"] == PaymentStatus.PENDING

    def test_the_sweep_keeps_the_order(self):
        order = _viva_order()
        api = _paid_at_viva(order)
        api.by_order_code[CODE][1]["Transactions"][0]["Amount"] = "n/a"

        with api:
            result = auto_cancel_stuck_pending_orders()

        assert result["skipped_open"] == 1
        assert result["canceled_pending"] == 0

    @pytest.mark.parametrize(
        "amount",
        [_ABSENT, None, "", float("nan"), float("inf")],
        ids=["absent", "null", "empty", "nan", "infinite"],
    )
    def test_a_finished_payment_with_no_finite_amount_keeps_the_order(
        self, amount
    ):
        """A finished payment Viva lists without an amount we can read is
        an answer that cannot be read, never "no payment": the sweep keeps
        the order instead of canceling it unpaid."""
        order = _viva_order()
        api = _paid_at_viva(order)
        row = api.by_order_code[CODE][1]["Transactions"][0]
        if amount is _ABSENT:
            del row["Amount"]
        else:
            row["Amount"] = amount

        with api:
            result = auto_cancel_stuck_pending_orders()

        assert result["errors"] == 0
        assert result["skipped_open"] == 1
        assert result["canceled_pending"] == 0
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING

    @pytest.mark.parametrize("status_id", ["R", "M", "MA", "MI", "ML", "MS"])
    def test_a_payment_refunded_or_disputed_since_keeps_the_order(
        self, status_id, caplog
    ):
        """Viva writes ``R`` (refunded in full or in part) or a dispute
        status on the ORIGINAL payment: the money was taken, so the order
        is never canceled as unpaid. No webhook settles such a payment, so
        nothing is applied or recorded for it: the order is kept for a
        person, with an ERROR."""
        order = _viva_order()
        api = _paid_at_viva(order)
        api.by_order_code[CODE][1]["Transactions"][0]["StatusId"] = status_id

        with api, caplog.at_level("ERROR", logger="order.payments"):
            result = auto_cancel_stuck_pending_orders()

        assert result["skipped_open"] == 1
        assert result["canceled_pending"] == 0
        assert not VivaWebhookEvent.objects.exists()
        assert TXN not in api.retrieved
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING
        assert any(r.levelname == "ERROR" for r in caplog.records)

    @pytest.mark.parametrize(
        "body",
        [
            {"ErrorCode": 0, "Success": True},
            {"Transactions": None, "Success": True},
            {"Transactions": {"TransactionId": TXN}, "Success": True},
        ],
        ids=["absent", "null", "not-a-list"],
    )
    def test_a_listing_without_its_transactions_keeps_the_order(self, body):
        """Only an explicit empty ``Transactions`` says nobody paid."""
        order = _viva_order()

        with _paid_at_viva(order, answer=(200, body)):
            result = auto_cancel_stuck_pending_orders()

        assert result["skipped_open"] == 1
        assert result["canceled_pending"] == 0
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING

    @pytest.mark.parametrize(
        "amount", [float("nan"), float("inf")], ids=["nan", "infinite"]
    )
    def test_a_verified_amount_that_is_no_number_is_flagged_for_staff(
        self, amount
    ):
        """Retrieve Transaction answering an amount that is not a finite
        number is refused like a malformed one: the charge is flagged for
        the store, the order kept, and nothing raises."""
        order = _viva_order()
        api = _paid_at_viva(order)
        api.transactions[TXN] = (
            200,
            retrieve_transaction(
                orderCode=int(CODE),
                amount=amount,
                statusId="F",
                currencyCode="978",
            ),
        )

        with api:
            result = auto_cancel_stuck_pending_orders()

        assert result["errors"] == 0
        assert result["canceled_pending"] == 0
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING
        assert payment_hold.awaits_payment_review(order)


class TestAViva:
    def test_replay_in_other_letter_case_changes_nothing(
        self, django_capture_on_commit_callbacks, confirmations, monkeypatch
    ):
        """Viva's ids are GUIDs, compared without regard to case: the same
        payment spelled in capitals is the same payment."""
        order = _viva_order()
        with (
            _paid_at_viva(order),
            django_capture_on_commit_callbacks(execute=True),
        ):
            auto_cancel_stuck_pending_orders()

        tenant = Tenant(
            schema_name="viva_case_replay",
            name="viva-case-replay",
            slug="viva-case-replay",
            owner_email="owner-viva-case-replay@example.com",
            is_active=True,
            suspended_at=None,
        )
        tenant.auto_create_schema = False
        tenant.save()

        @contextmanager
        def _here(_tenant):
            yield

        monkeypatch.setattr("order.views.viva_webhook.tenant_context", _here)
        api = _paid_at_viva(order)
        api.transactions[TXN.upper()] = api.transactions[TXN]
        with api, django_capture_on_commit_callbacks(execute=True):
            response = Client().post(
                reverse("viva-wallet-webhook"),
                data=json.dumps(
                    {
                        "EventTypeId": 1796,
                        "EventData": {
                            "TransactionId": TXN.upper(),
                            "OrderCode": CODE,
                            "StatusId": "F",
                        },
                    }
                ),
                content_type="application/json",
            )

        assert response.status_code == 200
        order.refresh_from_db()
        assert order.payment_id == TXN
        assert PaymentTransaction.objects.filter(order=order).count() == 1
        assert VivaWebhookEvent.objects.filter(order=order).count() == 1
        assert confirmations.call_count == 1

    def test_transaction_is_verified_before_the_order_row_is_locked(self):
        """Retrieve Transaction is an HTTP call, now also made for an
        anonymous return: a slow Viva must not hold the order row."""
        from order.payments import viva_settlement

        order = _viva_order()
        calls: list[str] = []
        verify = viva_settlement.verify_transaction
        lock = Order.objects.select_for_update

        def _verify(transaction_id):
            calls.append("verify")
            return verify(transaction_id)

        def _lock(*args, **kwargs):
            calls.append("lock")
            return lock(*args, **kwargs)

        with (
            _paid_at_viva(order),
            patch.object(viva_settlement, "verify_transaction", _verify),
            patch.object(Order.objects, "select_for_update", _lock),
        ):
            auto_cancel_stuck_pending_orders()

        assert calls[:2] == ["verify", "lock"]
