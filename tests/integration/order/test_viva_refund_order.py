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

from order.enum.status import OrderStatus, PaymentStatus
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


def _paid_viva_order(**kwargs):
    kwargs.setdefault(
        "pay_way", PayWayFactory.create_online_payment("viva_wallet")
    )
    kwargs.setdefault("status", OrderStatus.PROCESSING)
    kwargs.setdefault("payment_status", PaymentStatus.COMPLETED)
    kwargs.setdefault("payment_id", PAYMENT_ID)
    kwargs.setdefault("paid_amount", Money(Decimal("21.10"), "EUR"))
    return OrderFactory(num_order_items=0, **kwargs)


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
            success, response = OrderService.refund_order(
                order, reason="changed mind"
            )

        assert success is False
        assert response["error"] == "The transaction cannot be reversed."
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert "refunds" not in (order.metadata or {})
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
        assert "refunds" not in order.metadata
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
        fake = FakeVivaApi(captured=21.10)

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
        assert [r["refund_id"] for r in order.metadata["refunds"]] == [
            refund_id
        ]
        assert len(refunded_signals) == 1
        assert _refund_failure_notes(order) == []
