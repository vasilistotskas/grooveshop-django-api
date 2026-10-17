"""Viva disputes: the lifecycle read from the original payment's status.

Viva has no dispute webhook. Account Transaction Created (2054) fires on any
wallet movement and carries no OrderCode, so the dispute is followed through
``EventData.SaleTransactionId``: Retrieve Transaction for that sale reports
``M``/``MA``/``MI`` (open), ``ML`` (lost) or ``MW`` (won). A refund-claim
reversal (1797 of type 13/46/83/90) books the same chargeback under the same
key, so one dispute is never recorded twice. Only the HTTP boundary is faked
(``tests/utils/viva.py``).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import PaymentError
from order.factories import OrderFactory, PaymentTransactionFactory
from order.models import OrderHistory
from order.models.viva_webhook_event import VivaWebhookEvent
from order.payment_ledger import PaymentLedger
from order.services import OrderService
from order.views.viva_webhook import (
    _handle_account_transaction_event,
    _process_dispute_event_in_tenant,
    _process_event_in_tenant,
)
from pay_way.factories import PayWayFactory
from tests.utils.viva import (
    FakeVivaApi,
    retrieve_refund,
    retrieve_transaction,
    reversal_created_event,
)

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

SALE = "11111111-2222-3333-4444-555555555555"
CLAIM = "99999999-2222-3333-4444-555555555555"
ORDER_CODE = 7654321098
CAPTURED = 40.0

EMAIL = "order.signals.handlers.send_dispute_notification_email.apply_async"
TASK_EMAIL = "order.tasks.send_dispute_notification_email.apply_async"


@pytest.fixture
def refunded_signals():
    from order.signals import order_refunded

    received: list[dict] = []

    def receiver(sender, **kwargs):
        received.append(kwargs)

    order_refunded.connect(receiver, weak=False)
    yield received
    order_refunded.disconnect(receiver)


def _paid_order():
    order = OrderFactory(
        num_order_items=0,
        pay_way=PayWayFactory.create_online_payment("viva_wallet"),
        status=OrderStatus.PROCESSING,
        payment_status=PaymentStatus.COMPLETED,
        payment_id=SALE,
        amount_due=Money(Decimal(str(CAPTURED)), "EUR"),
        metadata={"viva_order_codes": [str(ORDER_CODE)]},
    )
    PaymentTransactionFactory(
        order=order,
        provider="viva_wallet",
        provider_reference=SALE,
        amount=Money(Decimal(str(CAPTURED)), "EUR"),
    )
    return order


def _sale(status_id: str):
    return (
        200,
        retrieve_transaction(
            amount=CAPTURED, orderCode=ORDER_CODE, statusId=status_id
        ),
    )


def _viva(status_id: str, **transactions):
    return FakeVivaApi(transactions={SALE: _sale(status_id), **transactions})


def _dispute(status_id: str, event_id: str):
    """Deliver a 2054 for the sale while Viva says it reads *status_id*."""
    with _viva(status_id), patch(EMAIL) as email:
        response = _process_dispute_event_in_tenant(
            sale_id=SALE, transaction_id=event_id
        )
    assert response.status_code == 200
    return email


def _chargebacks(order):
    return order.payment_transactions.filter(
        kind=PaymentTransactionKind.CHARGEBACK
    )


def _notes(order, fragment):
    return [
        h
        for h in OrderHistory.objects.filter(order=order, change_type="NOTE")
        if fragment in h.new_value["note"]
    ]


class TestDisputeOpened:
    @pytest.mark.parametrize("status_id", ["M", "MA", "MI"])
    def test_an_open_dispute_is_noted_and_staff_told_once(self, status_id):
        order = _paid_order()

        email = _dispute(status_id, "wallet-1")

        order.refresh_from_db()
        assert order.metadata["disputed"] is True
        assert order.metadata["dispute_id"] == SALE
        assert len(_notes(order, "dispute opened")) == 1
        email.assert_called_once()
        assert email.call_args.kwargs["args"] == [
            order.id,
            SALE,
            "viva_wallet",
        ]
        assert not _chargebacks(order).exists()

    def test_a_redelivered_event_adds_nothing(self):
        order = _paid_order()
        _dispute("M", "wallet-1")

        email = _dispute("M", "wallet-1")

        assert len(_notes(order, "dispute opened")) == 1
        email.assert_not_called()

    def test_another_event_of_the_same_dispute_is_not_announced_again(self):
        order = _paid_order()
        _dispute("M", "wallet-1")

        email = _dispute("MI", "wallet-2")

        assert len(_notes(order, "dispute opened")) == 1
        email.assert_not_called()
        order.refresh_from_db()
        assert order.metadata["dispute_status"] == "MI"


class TestDisputeLost:
    def test_one_chargeback_row_lowers_what_is_refundable(
        self, refunded_signals
    ):
        order = _paid_order()
        _dispute("M", "wallet-1")

        email = _dispute("ML", "wallet-2")

        [row] = _chargebacks(order)
        assert row.provider == "viva_wallet"
        assert row.provider_reference == SALE
        assert row.amount == Money(Decimal("40.00"), "EUR")
        assert row.charge.provider_reference == SALE
        assert PaymentLedger.refundable(order) == 0
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert refunded_signals == []
        assert OrderHistory.objects.filter(
            order=order,
            change_type=OrderHistory.OrderHistoryChangeType.CHARGEBACK,
        ).exists()
        email.assert_called_once()
        assert email.call_args.kwargs["kwargs"] == {"lost": True}
        assert not PaymentLedger.dispute_open(order, order.metadata)

    def test_a_refund_claim_for_the_same_sale_books_no_second_row(self):
        order = _paid_order()
        _dispute("ML", "wallet-2")

        with patch(EMAIL) as email:
            _claim(order)

        assert _chargebacks(order).count() == 1
        email.assert_not_called()

    def test_a_redelivered_lost_event_adds_nothing(self):
        order = _paid_order()
        _dispute("ML", "wallet-2")

        email = _dispute("ML", "wallet-2")

        assert _chargebacks(order).count() == 1
        email.assert_not_called()


class TestDisputeWon:
    def test_a_won_dispute_is_noted_and_books_no_row(self):
        order = _paid_order()
        _dispute("M", "wallet-1")

        email = _dispute("MW", "wallet-3")

        order.refresh_from_db()
        assert "disputed" not in order.metadata
        assert len(_notes(order, "dispute won")) == 1
        assert not _chargebacks(order).exists()
        email.assert_not_called()
        assert PaymentLedger.refundable(order) == Decimal("40.00")

    def test_a_won_event_for_an_unseen_dispute_is_noted_once(self):
        order = _paid_order()

        _dispute("MW", "wallet-3")
        _dispute("MW", "wallet-4")

        assert len(_notes(order, "dispute won")) == 1


class TestEventsThatAreNotOurDisputes:
    def test_a_sale_that_is_no_stores_payment_is_not_retrieved(self):
        with (
            patch(
                "order.views.viva_webhook._resolve_tenant_candidates_for_sale",
                return_value=[],
            ),
            _viva("ML") as fake,
        ):
            response = _handle_account_transaction_event(
                None, {"SaleTransactionId": "unknown", "TransactionId": "w"}
            )

        assert response.status_code == 200
        assert fake.retrieved == []

    def test_an_event_without_a_sale_transaction_does_nothing(self):
        with _viva("ML") as fake:
            response = _handle_account_transaction_event(
                None, {"TransactionId": "wallet-fee"}
            )

        assert response.status_code == 200
        assert fake.retrieved == []

    def test_a_clearance_of_a_finished_sale_does_nothing(self):
        order = _paid_order()

        email = _dispute("F", "wallet-clearance")

        order.refresh_from_db()
        assert "disputed" not in order.metadata
        assert not _chargebacks(order).exists()
        email.assert_not_called()

    def test_a_sale_of_another_order_code_is_refused(self):
        order = _paid_order()
        other = (
            200,
            retrieve_transaction(
                amount=CAPTURED, orderCode=ORDER_CODE + 1, statusId="ML"
            ),
        )

        with FakeVivaApi(transactions={SALE: other}), patch(EMAIL):
            _process_dispute_event_in_tenant(
                sale_id=SALE, transaction_id="wallet-9"
            )

        assert not _chargebacks(order).exists()

    def test_an_unverifiable_sale_answers_500_and_writes_nothing(self):
        order = _paid_order()

        with FakeVivaApi(transactions={SALE: (500, {})}), patch(EMAIL):
            response = _process_dispute_event_in_tenant(
                sale_id=SALE, transaction_id="wallet-9"
            )

        assert response.status_code == 500
        assert not _chargebacks(order).exists()
        assert not VivaWebhookEvent.objects.filter(
            transaction_id="wallet-9"
        ).exists()


def _claim(order, *, type_id=13, claim_id=CLAIM):
    """Deliver a 1797 whose retrieved transaction is a refund claim of the
    sale."""
    body = reversal_created_event(
        refund_id=claim_id, parent_id=SALE, order_code=ORDER_CODE
    )
    data = body["EventData"]
    claim = (
        200,
        retrieve_refund(amount=25.0, order_code=ORDER_CODE, type_id=type_id),
    )
    with FakeVivaApi(transactions={SALE: _sale("M"), claim_id: claim}):
        return _process_event_in_tenant(
            order_code=str(ORDER_CODE),
            event_type_id=1797,
            event_data=data,
            transaction_id=claim_id,
            status_id=data["StatusId"],
            txn_hash="deadbeef",
        )


class TestRefundClaimOn1797:
    def test_a_claim_books_one_chargeback_and_no_refund(self, refunded_signals):
        order = _paid_order()

        with patch(EMAIL) as email:
            response = _claim(order)

        assert response.status_code == 200
        [row] = _chargebacks(order)
        assert row.provider_reference == SALE
        assert row.amount == Money(Decimal("25.00"), "EUR")
        assert PaymentLedger.refundable(order) == Decimal("15.00")
        assert not order.payment_transactions.filter(
            kind=PaymentTransactionKind.REFUND
        ).exists()
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert refunded_signals == []
        email.assert_called_once()
        assert email.call_args.kwargs["kwargs"] == {"lost": True}
        assert OrderHistory.objects.filter(
            order=order,
            change_type=OrderHistory.OrderHistoryChangeType.CHARGEBACK,
        ).exists()

    def test_a_redelivered_claim_adds_nothing(self):
        order = _paid_order()
        _claim(order)

        with patch(EMAIL) as email:
            _claim(order)

        assert _chargebacks(order).count() == 1
        email.assert_not_called()

    def test_a_claim_then_a_lost_event_books_one_row(self):
        order = _paid_order()
        _claim(order)

        _dispute("ML", "wallet-5")

        assert _chargebacks(order).count() == 1


class TestRefundRefusedWhileDisputed:
    def test_a_viva_refund_is_refused_while_the_dispute_is_open(self):
        order = _paid_order()
        _dispute("M", "wallet-1")
        order.refresh_from_db()

        with pytest.raises(PaymentError, match="dispute is open"):
            OrderService.refund_order(order, reason="goodwill")

        assert not order.payment_transactions.filter(
            kind=PaymentTransactionKind.REFUND
        ).exists()

    def test_the_refusal_ends_when_the_dispute_does(self):
        order = _paid_order()
        _dispute("M", "wallet-1")
        _dispute("MW", "wallet-2")
        order.refresh_from_db()

        assert not PaymentLedger.dispute_open(order, order.metadata)

    def test_a_stripe_dispute_refuses_a_refund_too(self):
        order = OrderFactory(
            num_order_items=0,
            payment_status=PaymentStatus.COMPLETED,
            metadata={
                "disputed": True,
                "dispute_id": "dp_1",
                "dispute_status": "needs_response",
            },
        )

        assert PaymentLedger.dispute_open(order, order.metadata)

    def test_a_stripe_inquiry_does_not(self):
        order = OrderFactory(
            num_order_items=0,
            metadata={
                "disputed": True,
                "dispute_id": "dp_1",
                "dispute_status": "warning_needs_response",
            },
        )

        assert not PaymentLedger.dispute_open(order, order.metadata)
