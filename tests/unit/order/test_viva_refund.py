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

from order.payment import VivaWalletPaymentProvider
from tests.utils.viva import (
    CANCEL_ALREADY_CANCELLED,
    CANCEL_REFUSED,
    CANCEL_SUCCESS,
    FakeVivaApi,
)

PAYMENT_ID = "b1a3067c-321b-4ec6-bc9d-1778aef2a19d"
IDEMPOTENCY_KEY = "order-1-refund-1"


def _refund(fake: FakeVivaApi, amount: Money | None = None):
    with fake:
        return VivaWalletPaymentProvider().refund_payment(
            PAYMENT_ID,
            amount or Money(Decimal("21.10"), "EUR"),
            idempotency_key=IDEMPOTENCY_KEY,
        )


class TestTheRequestAlwaysCarriesAnAmount:
    @pytest.mark.parametrize(
        ("amount", "cents"),
        [
            (Money(Decimal("21.10"), "EUR"), "2110"),
            (Money(Decimal("5.25"), "EUR"), "525"),
            (Money(Decimal("0.01"), "EUR"), "1"),
        ],
    )
    def test_the_refund_sends_exactly_the_amount_asked_for(self, amount, cents):
        fake = FakeVivaApi()

        success, _data = _refund(fake, amount)

        assert success is True
        [delete] = fake.deletes
        assert fake.query(delete) == {"amount": [cents]}
        assert delete.url.startswith(
            f"https://demo.vivapayments.com/api/transactions/{PAYMENT_ID}?"
        )

    def test_the_amount_is_never_read_back_from_viva_first(self):
        """The ledger holds what was captured, so a refund is one call."""
        fake = FakeVivaApi()

        _refund(fake, Money(Decimal("5.25"), "EUR"))

        assert all(r.method != "GET" for r in fake.sent)

    def test_the_idempotency_key_is_not_sent(self):
        """``DELETE /api/transactions/{id}`` has no such parameter (Viva's
        ``idempotencyKey`` belongs to ``/acquiring/v1/transactions/{id}``),
        so a key that went out would only be an unknown query parameter."""
        fake = FakeVivaApi()

        _refund(fake)

        [delete] = fake.deletes
        assert "idempotencyKey" not in fake.query(delete)
        assert IDEMPOTENCY_KEY not in (delete.url or "")


class TestOnlyADocumentedSuccessCounts:
    def test_the_refund_id_is_the_returned_transaction_id(self):
        success, data = _refund(FakeVivaApi())

        assert success is True
        assert data == {
            "refund_id": CANCEL_SUCCESS["TransactionId"],
            "amount": "21.10",
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
