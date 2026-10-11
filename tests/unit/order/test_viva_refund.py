"""``VivaWalletPaymentProvider.refund_payment`` against Viva's documented
Cancel Transaction contract.

Production, 2026-10-10: every in-app Viva refund booked the order
REFUNDED while Viva still showed the payment as ``F``. The request went
out without ``amount`` (``required: true`` in the spec) and the answer was
trusted on its HTTP status alone. Only the HTTP boundary is faked
(``tests/utils/viva.py``); the bodies are the spec's.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from order.enum.status import PaymentStatus
from order.payment import VivaWalletPaymentProvider
from tests.utils.viva import (
    CANCEL_ALREADY_CANCELLED,
    CANCEL_REFUSED,
    CANCEL_SUCCESS,
    FakeVivaApi,
)

PAYMENT_ID = "b1a3067c-321b-4ec6-bc9d-1778aef2a19d"


def _refund(fake: FakeVivaApi, amount: Money | None = None):
    with fake:
        return VivaWalletPaymentProvider().refund_payment(PAYMENT_ID, amount)


class TestTheRequestAlwaysCarriesAnAmount:
    def test_a_full_refund_names_the_amount_viva_captured(self):
        fake = FakeVivaApi(captured=21.1)

        success, _data = _refund(fake)

        assert success is True
        [delete] = fake.deletes
        assert fake.query(delete) == {"amount": ["2110"]}
        assert delete.url.startswith(
            f"https://demo.vivapayments.com/api/transactions/{PAYMENT_ID}?"
        )

    def test_a_partial_refund_sends_what_was_asked_without_a_lookup(self):
        fake = FakeVivaApi()

        success, _data = _refund(fake, Money(Decimal("5.25"), "EUR"))

        assert success is True
        [delete] = fake.deletes
        assert fake.query(delete) == {"amount": ["525"]}
        assert all(r.method != "GET" for r in fake.sent)

    @pytest.mark.parametrize(
        "amount",
        [None, Money(Decimal("10.00"), "EUR"), Money(Decimal(0), "EUR")],
        ids=["full", "partial", "zero"],
    )
    def test_no_delete_goes_out_without_amount(self, amount):
        fake = FakeVivaApi()

        _refund(fake, amount)

        assert fake.deletes
        assert all("amount" in fake.query(r) for r in fake.deletes)

    @pytest.mark.parametrize(
        "retrieve",
        [
            (404, ["Transaction Id does not exist."]),
            (200, {"statusId": "F"}),
        ],
        ids=["retrieve-fails", "retrieve-has-no-amount"],
    )
    def test_a_full_refund_that_cannot_learn_the_amount_sends_nothing(
        self, retrieve
    ):
        fake = FakeVivaApi(retrieve=retrieve)

        success, data = _refund(fake)

        assert success is False
        assert data["viva_error"] is True
        assert fake.deletes == []


class TestOnlyADocumentedSuccessCounts:
    def test_the_refund_id_is_the_returned_transaction_id(self):
        success, data = _refund(FakeVivaApi(captured=21.1))

        assert success is True
        assert data == {
            "refund_id": CANCEL_SUCCESS["TransactionId"],
            "status": PaymentStatus.REFUNDED,
            "amount": "21.1",
            "payment_id": PAYMENT_ID,
            "viva_status": "refunded",
        }

    def test_http_200_with_success_false_is_a_failed_refund(self):
        fake = FakeVivaApi(cancel=(200, CANCEL_REFUSED))

        success, data = _refund(fake)

        assert success is False
        assert data == {
            "error": "The transaction cannot be reversed.",
            "viva_error": True,
            "error_code": 1403,
            "event_id": 2701,
        }
        assert "refund_id" not in data

    @pytest.mark.parametrize(
        "body",
        [
            {**CANCEL_SUCCESS, "Success": False},
            {**CANCEL_SUCCESS, "ErrorCode": 1403},
            {**CANCEL_SUCCESS, "Success": "true"},
            {k: v for k, v in CANCEL_SUCCESS.items() if k != "ErrorCode"},
            {k: v for k, v in CANCEL_SUCCESS.items() if k != "Success"},
        ],
        ids=[
            "success-false",
            "error-code-set",
            "success-not-a-boolean",
            "no-error-code",
            "no-success",
        ],
    )
    def test_both_success_and_a_zero_error_code_are_required(self, body):
        success, data = _refund(FakeVivaApi(cancel=(200, body)))

        assert success is False
        assert data["viva_error"] is True
        assert "refund_id" not in data

    def test_a_refusal_without_error_text_still_says_something(self):
        refused = {**CANCEL_REFUSED, "ErrorText": None}

        _success, data = _refund(FakeVivaApi(cancel=(200, refused)))

        assert data["error"] == "Viva Wallet refused the refund."

    def test_an_http_error_keeps_the_body_for_the_order_history(self):
        fake = FakeVivaApi(cancel=(403, CANCEL_ALREADY_CANCELLED))

        success, data = _refund(fake)

        assert success is False
        assert data["viva_error"] is True
        assert data["details"] == CANCEL_ALREADY_CANCELLED
