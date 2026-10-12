"""Cancelling and refunding a paid Viva Wallet order.

Production, 2026-10-10: all four REFUNDED orders were Viva in-app cancels
whose ``metadata.refunds[0].refund_id`` is null, while Viva still showed
each payment as ``F``. A refund Viva refuses must leave the order
un-refunded, say so to staff, and fire none of the ``order_refunded``
side effects (refund email, gift-card credit, loyalty reversal, Meta
Refund). Only the HTTP boundary is faked (``tests/utils/viva.py``).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from djmoney.money import Money
from rest_framework.test import APIClient

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import PaymentTransactionFactory
from order.factories.order import OrderFactory
from order.models.history import OrderHistory
from order.models.order import Order
from order.services import OrderService
from order.signals import order_refunded
from pay_way.factories import PayWayFactory
from tests.utils.viva import (
    CANCEL_ALREADY_CANCELLED,
    CANCEL_REFUSED,
    CANCEL_SUCCESS,
    FakeVivaApi,
)
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

PAYMENT_ID = "b1a3067c-321b-4ec6-bc9d-1778aef2a19d"
FAILED_REFUND = {
    "refunded": False,
    "error": "The refund could not be processed.",
    "message": "Order canceled but refund failed",
}


@pytest.fixture
def refunded_signals():
    """Every ``order_refunded`` the test caused."""
    received: list[dict] = []

    def receiver(sender, **kwargs):
        received.append(kwargs)

    order_refunded.connect(receiver, weak=False)
    yield received
    order_refunded.disconnect(receiver)


def _paid_viva_order(*, captured="21.10", ledger=True, **kwargs):
    """A paid Viva order. ``captured`` is what the ledger says Viva took;
    ``ledger=False`` leaves the capture unrecorded (the order was settled
    while the previous release served, say)."""
    kwargs.setdefault(
        "pay_way", PayWayFactory.create_online_payment("viva_wallet")
    )
    kwargs.setdefault("status", OrderStatus.PROCESSING)
    kwargs.setdefault("payment_status", PaymentStatus.COMPLETED)
    kwargs.setdefault("payment_id", PAYMENT_ID)
    kwargs.setdefault("paid_amount", Money(Decimal("21.10"), "EUR"))
    order = OrderFactory(num_order_items=0, **kwargs)
    if ledger:
        PaymentTransactionFactory(
            order=order,
            provider="viva_wallet",
            provider_reference=PAYMENT_ID,
            amount=Money(Decimal(captured), "EUR"),
        )
    return order


def _refund_rows(order: Order):
    return order.payment_transactions.filter(kind=PaymentTransactionKind.REFUND)


def _refund_failure_notes(order: Order) -> list[str]:
    """The history notes that record a refund the provider did not make
    (the order also collects unrelated notes, e.g. one per email sent)."""
    return [
        note
        for note in (
            row.new_value["note"]
            for row in OrderHistory.objects.filter(
                order=order, change_type="NOTE"
            )
        )
        if note.startswith("Refund via ")
    ]


class TestARefundVivaRefusesLeavesTheOrderUnrefunded:
    def test_http_200_with_success_false_does_not_refund_the_order(
        self, refunded_signals
    ):
        order = _paid_viva_order()

        with FakeVivaApi(cancel=(200, CANCEL_REFUSED)):
            result = OrderService.refund_order(order, reason="changed mind")

        assert result.complete is False
        assert result.paid_something is False
        assert result.declined["error"] == "The transaction cannot be reversed."
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert not _refund_rows(order).exists()
        assert refunded_signals == []

    def test_cancel_reports_the_refund_as_not_done_and_fires_nothing(
        self, refunded_signals
    ):
        order = _paid_viva_order()

        with FakeVivaApi(cancel=(200, CANCEL_REFUSED)):
            canceled, refund_info = OrderService.cancel_order(
                order, reason="changed mind"
            )

        assert refund_info == FAILED_REFUND
        assert canceled.status == OrderStatus.CANCELED
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        # Money taken, none returned: the payment must still read COMPLETED.
        assert order.payment_status == PaymentStatus.COMPLETED
        assert not _refund_rows(order).exists()
        assert refunded_signals == []

    def test_staff_find_the_reason_in_the_order_history(self):
        order = _paid_viva_order()

        with FakeVivaApi(cancel=(200, CANCEL_REFUSED)):
            OrderService.cancel_order(order, reason="changed mind")

        assert _refund_failure_notes(order) == [
            (
                "Refund via viva_wallet was not processed: "
                "The transaction cannot be reversed."
            )
        ]

    def test_an_http_error_body_reaches_the_order_history_too(self):
        order = _paid_viva_order()

        with FakeVivaApi(cancel=(403, CANCEL_ALREADY_CANCELLED)):
            _canceled, refund_info = OrderService.cancel_order(order)

        assert refund_info == FAILED_REFUND
        [note] = _refund_failure_notes(order)
        assert note.startswith("Refund via viva_wallet was not processed: ")
        assert CANCEL_ALREADY_CANCELLED in note

    def test_the_customer_sees_neither_viva_s_text_nor_the_staff_note(self):
        user = UserAccountFactory()
        order = _paid_viva_order(user=user)
        client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
        client.force_authenticate(user=user)

        with FakeVivaApi(cancel=(200, CANCEL_REFUSED)):
            response = client.post(
                reverse("order-cancel", kwargs={"pk": order.pk}),
                {"reason": "changed mind"},
                format="json",
            )

        assert response.status_code == 200, response.data
        assert response.data["refund_info"] == FAILED_REFUND
        assert "cannot be reversed" not in str(response.data)
        assert "was not processed" not in str(response.data)


class TestARefundVivaAcceptsIsRecorded:
    def test_cancel_refunds_the_captured_amount_and_keeps_the_refund_id(
        self, refunded_signals
    ):
        order = _paid_viva_order()
        fake = FakeVivaApi()

        with fake:
            canceled, refund_info = OrderService.cancel_order(
                order, reason="changed mind"
            )

        refund_id = CANCEL_SUCCESS["TransactionId"]
        assert refund_info == {
            "refunded": True,
            "refund_id": refund_id,
            "message": "Payment refunded successfully",
        }
        [delete] = fake.deletes
        assert fake.query(delete) == {"amount": ["2110"]}
        order.refresh_from_db()
        assert canceled.status == OrderStatus.CANCELED
        assert order.payment_status == PaymentStatus.REFUNDED
        [refund] = _refund_rows(order)
        assert refund.provider_reference == refund_id
        assert refund.provider == "viva_wallet"
        assert refund.description == "Order canceled: changed mind"
        # ``order_refunded`` always says how much came back; a full refund
        # sends what the order paid.
        assert [r["amount"] for r in refunded_signals] == [
            Money(Decimal("21.10"), "EUR")
        ]
        assert _refund_failure_notes(order) == []

    def test_a_full_refund_is_what_the_ledger_captured_not_what_was_billed(
        self, refunded_signals
    ):
        """``Order.paid_amount`` is the amount owed when the order was
        created. The refund, and what ``order_refunded`` reports, is the
        amount the ledger holds for the payment."""
        order = _paid_viva_order(captured="10.00")
        assert order.paid_amount == Money(Decimal("21.10"), "EUR")
        fake = FakeVivaApi()

        with fake:
            OrderService.cancel_order(order, reason="changed mind")

        [delete] = fake.deletes
        assert fake.query(delete) == {"amount": ["1000"]}
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        assert [Decimal(r.amount.amount) for r in _refund_rows(order)] == [
            Decimal(10)
        ]
        assert [r["amount"] for r in refunded_signals] == [
            Money(Decimal(10), "EUR")
        ]

    def test_a_part_refund_is_partially_refunded_not_refunded(
        self, refunded_signals
    ):
        """Viva answers success for a part refund exactly as for a full
        one, and REFUNDED is what releases the gift-card redemption and
        the loyalty points — so it has to mean "nothing left to refund"."""
        order = _paid_viva_order()
        fake = FakeVivaApi()

        with fake:
            result = OrderService.refund_order(
                order, amount=Money(Decimal("5.00"), "EUR")
            )

        assert result.complete is True
        assert result.refunded == Money(Decimal("5.00"), "EUR")
        [delete] = fake.deletes
        assert fake.query(delete) == {"amount": ["500"]}
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        assert [r.amount.amount for r in _refund_rows(order)] == [
            Decimal("5.00")
        ]
        assert [r["amount"] for r in refunded_signals] == [
            Money(Decimal("5.00"), "EUR")
        ]
        # The ledger already held the capture: Viva is not asked for it.
        assert fake.retrieved == []


class TestACaptureTheLedgerNeverRecorded:
    """An order settled without a CHARGE row (the payment landed while the
    previous release was serving, or the success event never reached the
    order) is read from Viva once, recorded, and refunded from the ledger."""

    def test_the_capture_is_read_from_viva_and_recorded(self, refunded_signals):
        order = _paid_viva_order(ledger=False)
        fake = FakeVivaApi(captured=21.10)

        with fake:
            result = OrderService.refund_order(
                order, amount=Money(Decimal("5.00"), "EUR")
            )

        assert result.complete is True
        assert fake.retrieved == [PAYMENT_ID]
        [charge] = order.payment_transactions.filter(
            kind=PaymentTransactionKind.CHARGE
        )
        assert charge.provider == "viva_wallet"
        assert charge.provider_reference == PAYMENT_ID
        assert charge.amount == Money(Decimal("21.10"), "EUR")
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED

    def test_an_unreadable_capture_refuses_the_refund_and_sends_nothing(
        self, refunded_signals
    ):
        """Nothing is sent: once the money has moved, a figure that could
        not be read could no longer be undone."""
        order = _paid_viva_order(ledger=False)
        fake = FakeVivaApi(retrieve=(404, ["Transaction Id does not exist."]))

        with fake:
            result = OrderService.refund_order(
                order, amount=Money(Decimal("5.00"), "EUR")
            )

        assert result.complete is False
        assert "captured" in result.declined["error"]
        assert fake.deletes == []
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert not order.payment_transactions.exists()
        assert refunded_signals == []
        assert len(_refund_failure_notes(order)) == 1
