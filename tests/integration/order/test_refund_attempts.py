"""``RefundService.refund_order`` claims, calls and records (H1).

A connection idle in a transaction is killed after ten seconds
(``idle_in_transaction_session_timeout``), so the provider must be called
with no transaction open. Between the claim and the record the refund is a
``RefundAttempt``: it holds its amount back, its key is what the provider
receives, and a retry, a double click or the provider's own webhook
settles it without a second refund.
"""

from __future__ import annotations

from decimal import Decimal
from unittest import mock

import pytest
import stripe
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from djmoney.money import Money

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import PaymentError
from order.factories import OrderFactory, PaymentTransactionFactory
from order.models.history import OrderHistory
from order.models.refund_attempt import RefundAttempt, RefundAttemptStatus
from order.payments.payment_ledger import PaymentLedger
from order.refunds.service import RefundService
from order.services import OrderService
from pay_way.factories import PayWayFactory
from tests.utils.stripe_refunds import FakeStripeRefunds
from tests.utils.viva import CANCEL_SUCCESS, FakeVivaApi

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

REFUND = PaymentTransactionKind.REFUND
VIVA_PAYMENT = "b1a3067c-321b-4ec6-bc9d-1778aef2a19d"


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def _order(captured="100.00", **kwargs):
    kwargs.setdefault("pay_way", PayWayFactory.create_online_payment())
    kwargs.setdefault("status", OrderStatus.DELIVERED)
    kwargs.setdefault("payment_status", PaymentStatus.COMPLETED)
    kwargs.setdefault("payment_id", "pi_ledger")
    kwargs.setdefault("amount_due", _eur(captured))
    order = OrderFactory(num_order_items=0, **kwargs)
    PaymentTransactionFactory(
        order=order, amount=_eur(captured), provider_reference="pi_ledger"
    )
    return order


def _viva_order(captured="100.00"):
    order = OrderFactory(
        num_order_items=0,
        pay_way=PayWayFactory.create_online_payment("viva_wallet"),
        status=OrderStatus.DELIVERED,
        payment_status=PaymentStatus.COMPLETED,
        payment_id=VIVA_PAYMENT,
        amount_due=_eur(captured),
    )
    PaymentTransactionFactory(
        order=order,
        provider="viva_wallet",
        provider_reference=VIVA_PAYMENT,
        amount=_eur(captured),
    )
    return order


def _rows(order):
    return order.payment_transactions.filter(kind=REFUND)


class _TransactionWatchingStripe(FakeStripeRefunds):
    """A Stripe that notes whether the caller held a transaction open."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.in_transaction: list[bool] = []

    def _create(self, **kwargs):
        self.in_transaction.append(connection.in_atomic_block)
        return super()._create(**kwargs)


@pytest.mark.django_db(transaction=True)
class TestNoTransactionIsOpenWhileTheProviderAnswers:
    def test_refund_order_calls_the_provider_outside_a_transaction(self):
        order = _order()

        with _TransactionWatchingStripe() as stripe_api:
            result = RefundService.refund_order(order, amount=_eur("30.00"))

        assert result.complete is True
        assert stripe_api.in_transaction == [False]
        assert [r.amount.amount for r in _rows(order)] == [Decimal("30.00")]

    def test_a_cancel_refunds_after_it_has_committed(self):
        order = _order(status=OrderStatus.PROCESSING)

        with _TransactionWatchingStripe() as stripe_api:
            canceled, refund_info = OrderService.cancel_order(
                order, reason="changed mind", customer_initiated=False
            )

        assert refund_info["refunded"] is True
        assert canceled.status == OrderStatus.CANCELED
        assert stripe_api.in_transaction == [False]

    def test_a_cancel_whose_refund_fails_is_not_marked_refunded(self):
        # Orders canceled in April 2026 were marked REFUNDED with no refund
        # id and nothing refunded at the provider: the status is derived
        # from the ledger now, and a refund the provider did not make writes
        # no row.
        order = _order(status=OrderStatus.PROCESSING)

        with FakeStripeRefunds(status="failed"):
            canceled, refund_info = OrderService.cancel_order(
                order, reason="changed mind", customer_initiated=False
            )

        assert refund_info["refunded"] is False
        assert canceled.status == OrderStatus.CANCELED
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert not _rows(order)


class TestTheAttemptHoldsItsAmountBack:
    def test_a_claimed_share_cannot_be_refunded_twice(self):
        order = _order()
        with FakeStripeRefunds():
            with mock.patch.object(
                PaymentLedger, "record_refund", side_effect=RuntimeError("db")
            ):
                with pytest.raises(RuntimeError):
                    RefundService.refund_order(order, amount=_eur("80.00"))

        # 80 is on its way out: only 20 is left to claim.
        assert PaymentLedger.refundable(order) == Decimal("20.00")
        with FakeStripeRefunds() as stripe_api, pytest.raises(PaymentError):
            RefundService.refund_order(order, amount=_eur("30.00"))
        assert stripe_api.calls == []

    def test_an_old_attempt_still_holds_its_amount(self):
        """No timer frees it: only a settled or declined attempt lets go."""
        order = _order()
        charge = order.payment_transactions.get()
        attempt = RefundAttempt.objects.create(
            order=order, charge=charge, amount=_eur("80.00")
        )
        RefundAttempt.objects.filter(pk=attempt.pk).update(
            created_at=attempt.created_at.replace(year=2000)
        )

        assert PaymentLedger.refundable(order) == Decimal("20.00")

    def test_a_refusal_frees_what_the_attempt_held(self):
        order = _order()

        with FakeStripeRefunds(fail_with=stripe.InvalidRequestError("no", "x")):
            result = RefundService.refund_order(order, amount=_eur("40.00"))

        assert result.complete is False
        [attempt] = order.refund_attempts.all()
        assert attempt.status == RefundAttemptStatus.DECLINED
        assert PaymentLedger.refundable(order) == Decimal("100.00")

    def test_an_unknown_outcome_keeps_it_held(self):
        """A dropped connection may follow a refund Stripe made: the amount
        stays held until the webhook or a retry settles it."""
        order = _order()

        with FakeStripeRefunds(fail_with=stripe.APIConnectionError("drop")):
            result = RefundService.refund_order(order, amount=_eur("40.00"))

        assert result.complete is False
        assert result.declined["outcome_unknown"] is True
        [attempt] = order.refund_attempts.all()
        assert attempt.status == RefundAttemptStatus.PENDING
        assert PaymentLedger.refundable(order) == Decimal("60.00")
        [note] = [
            row.new_value["note"]
            for row in OrderHistory.objects.filter(
                order=order, change_type="NOTE"
            )
            if row.new_value["note"].startswith("Refund via ")
        ]
        assert "unknown outcome" in note

    def test_later_shares_are_released_when_one_is_not_made(self):
        order = _order("60.00")
        PaymentTransactionFactory(
            order=order, amount=_eur("40.00"), provider_reference="pi_second"
        )

        with FakeStripeRefunds(fail_with=stripe.InvalidRequestError("no", "x")):
            result = RefundService.refund_order(order)

        assert result.complete is False
        assert {a.status for a in order.refund_attempts.all()} == {
            RefundAttemptStatus.DECLINED
        }
        assert PaymentLedger.refundable(order) == Decimal("100.00")


class TestARepeatReusesTheAttempt:
    def test_a_double_click_while_the_first_is_unsettled_is_one_refund(self):
        order = _order()
        with FakeStripeRefunds() as stripe_api:
            with mock.patch.object(
                PaymentLedger, "record_refund", side_effect=RuntimeError("db")
            ):
                with pytest.raises(RuntimeError):
                    RefundService.refund_order(order, amount=_eur("40.00"))
            result = RefundService.refund_order(order, amount=_eur("40.00"))

        assert result.complete is True
        assert order.refund_attempts.count() == 1
        assert len(stripe_api.created) == 1
        assert len(_rows(order)) == 1
        attempt = order.refund_attempts.get()
        assert attempt.status == RefundAttemptStatus.SETTLED
        assert attempt.refund == _rows(order).get()

    def test_a_full_refund_resumes_what_holds_everything(self):
        order = _order()
        with FakeStripeRefunds() as stripe_api:
            with mock.patch.object(
                PaymentLedger, "record_refund", side_effect=RuntimeError("db")
            ):
                with pytest.raises(RuntimeError):
                    RefundService.refund_order(order)
            result = RefundService.refund_order(order)

        assert result.refunded == _eur("100.00")
        assert len(stripe_api.created) == 1
        assert order.refund_attempts.count() == 1

    def test_a_provider_without_idempotency_is_never_asked_twice(self):
        """Viva takes no idempotency key: a lost answer cannot be told from
        a refund never made, so the retry waits for the 1797 webhook."""
        order = _viva_order()
        api = FakeVivaApi(captured=100.0)
        with (
            api,
            mock.patch.object(
                PaymentLedger, "record_refund", side_effect=RuntimeError("db")
            ),
        ):
            with pytest.raises(RuntimeError):
                RefundService.refund_order(order, amount=_eur("40.00"))

        def cancels() -> int:
            return sum(1 for r in api.sent if r.method == "DELETE")

        assert cancels() == 1

        with api, pytest.raises(PaymentError) as excinfo:
            RefundService.refund_order(order, amount=_eur("40.00"))

        assert "result is not known yet" in str(excinfo.value)
        assert cancels() == 1


class TestTheWebhookBeatsTheRecord:
    def test_a_refund_the_webhook_booked_mid_call_is_not_booked_twice(
        self, refunded_signals
    ):
        order = _order()
        charge = order.payment_transactions.get()

        class _Stripe(FakeStripeRefunds):
            def _create(self, **kwargs):
                refund = super()._create(**kwargs)
                # ``charge.refunded`` lands while the call is in flight,
                # before the caller can record it.
                PaymentLedger.record_refund(
                    order,
                    charge=charge,
                    amount=_eur("40.00"),
                    reference="evt_webhook",
                )
                return refund

        with _Stripe():
            result = RefundService.refund_order(order, amount=_eur("40.00"))

        assert result.complete is True
        assert result.refunded == _eur("40.00")
        assert [r.provider_reference for r in _rows(order)] == ["evt_webhook"]
        attempt = order.refund_attempts.get()
        assert attempt.status == RefundAttemptStatus.SETTLED
        assert PaymentLedger.refundable(order) == Decimal("60.00")
        # The webhook announces the refund; the caller does not again.
        assert refunded_signals == []

    def test_a_webhook_refund_settles_the_matching_attempt(self):
        order = _order()
        charge = order.payment_transactions.get()
        attempt = RefundAttempt.objects.create(
            order=order, charge=charge, amount=_eur("40.00")
        )

        row, created = PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("40.00"), reference="re_x"
        )

        assert created is True
        attempt.refresh_from_db()
        assert attempt.status == RefundAttemptStatus.SETTLED
        assert attempt.refund == row


class TestWhatTheProviderAnswers:
    def test_viva_accepting_a_refund_without_naming_it_books_nothing(self):
        """Money may have moved, but there is no id to book it under that
        its own reversal webhook would repeat."""
        order = _viva_order()
        body = {**CANCEL_SUCCESS, "TransactionId": None}

        with FakeVivaApi(captured=100.0, cancel=(200, body)):
            result = RefundService.refund_order(order, amount=_eur("40.00"))

        assert result.complete is False
        assert result.declined["outcome_unknown"] is True
        assert not _rows(order).exists()
        assert order.refund_attempts.get().status == (
            RefundAttemptStatus.PENDING
        )

    def test_a_store_without_credentials_gets_a_clean_decline(self):
        order = _order()

        with mock.patch(
            "order.payments.payment.get_payment_provider",
            side_effect=ImproperlyConfigured("no key"),
        ):
            result = RefundService.refund_order(order, amount=_eur("40.00"))

        assert result.complete is False
        assert result.paid_something is False
        assert "No credentials" in result.declined["error"]
        assert order.refund_attempts.get().status == (
            RefundAttemptStatus.DECLINED
        )

    def test_the_viva_provider_itself_reports_that_outcome_as_unknown(self):
        from order.payments.payment import VivaWalletPaymentProvider

        body = {**CANCEL_SUCCESS, "TransactionId": None}

        with FakeVivaApi(captured=100.0, cancel=(200, body)):
            success, response = VivaWalletPaymentProvider().refund_payment(
                VIVA_PAYMENT, _eur("40.00"), idempotency_key="unused"
            )

        assert success is False
        assert response["outcome_unknown"] is True


def _oauth_only():
    """A store whose Viva settings hold the OAuth pair alone: it was paid
    through it while the offer needed only that pair."""
    from tests.utils.viva import CREDENTIALS

    return mock.patch(
        "tenant.credentials.viva_wallet_credentials",
        return_value={**CREDENTIALS, "merchant_id": "", "api_key": ""},
    )


class TestOnlyARequestThatMayHaveReachedVivaIsUnknown:
    """A refund that was never sent is a refusal: it frees what its
    attempt held at once and tells staff why (the stand-in review of
    3cc73abb7). Only a request that may have reached Viva is unknown."""

    def test_a_store_without_the_basic_pair_is_refused_before_sending(self):
        order = _viva_order()
        api = FakeVivaApi(captured=100.0)

        with api, _oauth_only():
            result = RefundService.refund_order(order, amount=_eur("40.00"))

        assert not any(r.method == "DELETE" for r in api.sent)
        assert result.complete is False
        assert not result.declined.get("outcome_unknown")
        assert order.refund_attempts.get().status == (
            RefundAttemptStatus.DECLINED
        )
        assert PaymentLedger.refundable(order) == Decimal("100.00")
        [note] = [
            row.new_value["note"]
            for row in OrderHistory.objects.filter(
                order=order, change_type="NOTE"
            )
            if row.new_value["note"].startswith("Refund via ")
        ]
        assert "was not processed" in note
        assert "Merchant ID and API Key are missing" in note

    def test_a_connection_never_made_is_a_refusal(self):
        import requests

        from order.payments.payment import VivaWalletPaymentProvider

        with (
            FakeVivaApi(captured=100.0),
            mock.patch("requests.delete", side_effect=requests.ConnectTimeout),
        ):
            success, response = VivaWalletPaymentProvider().refund_payment(
                VIVA_PAYMENT, _eur("40.00"), idempotency_key="unused"
            )

        assert success is False
        assert not response.get("outcome_unknown")

    def test_an_answer_that_never_came_stays_unknown(self):
        import requests

        from order.payments.payment import VivaWalletPaymentProvider

        with (
            FakeVivaApi(captured=100.0),
            mock.patch("requests.delete", side_effect=requests.ReadTimeout),
        ):
            success, response = VivaWalletPaymentProvider().refund_payment(
                VIVA_PAYMENT, _eur("40.00"), idempotency_key="unused"
            )

        assert success is False
        assert response["outcome_unknown"] is True

    def test_the_refund_lookup_without_the_basic_pair_sends_nothing(self):
        from order.payments.payment import VivaWalletPaymentProvider

        order = _viva_order()
        order.metadata = {"viva_order_codes": ["1111"]}
        order.save(update_fields=["metadata"])
        api = FakeVivaApi(captured=100.0)

        with api, _oauth_only():
            found = VivaWalletPaymentProvider().find_refunds(
                order, VIVA_PAYMENT, _eur("40.00"), idempotency_key=""
            )

        assert found is None
        assert not any("/api/transactions/" in r.url for r in api.sent)
