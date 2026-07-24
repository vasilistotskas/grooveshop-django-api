"""Regression tests for Viva webhook reversal/failure verification (G0275).

The Viva webhook endpoint is unauthenticated (no HMAC; the source-IP check
is non-blocking), so the reversal (1797) and failed (1798) handlers must
verify the transaction against Viva's Retrieve Transaction API before
mutating ``payment_status`` — a spoofed event must not be able to flip an
order to REFUNDED/FAILED.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.views.viva_webhook import (
    _handle_payment_failed,
    _handle_reversal_created,
)


@pytest.mark.django_db
class TestVivaReversalVerification:
    def _order(self):
        return OrderFactory(
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
            payment_id="viva_txn_1",
            metadata={},
        )

    def test_reversal_flips_to_refunded_when_verified(self):
        order = self._order()
        with (
            patch(
                "order.views.viva_webhook._verify_transaction",
                return_value=(PaymentStatus.REFUNDED, {"order_code": "OC1"}),
            ),
            patch("order.signals.order_refunded.send") as mock_signal,
        ):
            _handle_reversal_created(order, {}, "viva_txn_1")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        assert order.metadata["refunds"][0]["reversal_transaction_id"] == (
            "viva_txn_1"
        )
        mock_signal.assert_called_once()

    def test_reversal_skipped_when_transaction_not_refunded(self):
        """A spoofed reversal referencing a still-COMPLETED transaction is
        not honoured — payment_status stays COMPLETED."""
        order = self._order()
        with (
            patch(
                "order.views.viva_webhook._verify_transaction",
                return_value=(PaymentStatus.COMPLETED, {"order_code": "OC1"}),
            ),
            patch("order.signals.order_refunded.send") as mock_signal,
        ):
            _handle_reversal_created(order, {}, "viva_txn_1")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert "refunds" not in (order.metadata or {})
        mock_signal.assert_not_called()

    def test_reversal_raises_when_verification_unavailable(self):
        """A forged/unknown transaction id (Viva returns an error row) must
        raise so the outer atomic rolls back and Viva retries — never a
        silent state flip."""
        order = self._order()
        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(PaymentStatus.FAILED, {"viva_error": True}),
        ):
            with pytest.raises(RuntimeError):
                _handle_reversal_created(order, {}, "forged_txn")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED

    def test_reversal_skipped_when_no_transaction_id(self):
        order = self._order()
        with patch(
            "order.views.viva_webhook._verify_transaction"
        ) as mock_verify:
            _handle_reversal_created(order, {}, "")

        mock_verify.assert_not_called()
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED


@pytest.mark.django_db
class TestVivaPaymentFailedVerification:
    def _order(self):
        return OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id="viva_txn_2",
            metadata={},
        )

    def test_failed_flips_to_failed_when_verified(self):
        order = self._order()
        with (
            patch(
                "order.views.viva_webhook._verify_transaction",
                return_value=(PaymentStatus.FAILED, {"order_code": "OC2"}),
            ),
            patch(
                "order.views.viva_webhook.send_payment_failed_email.delay"
            ) as mock_email,
        ):
            _handle_payment_failed(order, {}, "viva_txn_2")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.FAILED
        mock_email.assert_called_once_with(order.id)

    def test_failed_raises_when_verification_returns_error(self):
        """A network blip during verification maps to FAILED with an
        ``error`` key — that must be treated as unavailable (raise), not as
        a confirmed failure."""
        order = self._order()
        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(PaymentStatus.FAILED, {"error": "timeout"}),
        ):
            with pytest.raises(RuntimeError):
                _handle_payment_failed(order, {}, "viva_txn_2")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING

    def test_failed_skipped_when_transaction_still_completed(self):
        order = self._order()
        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(PaymentStatus.COMPLETED, {"order_code": "OC2"}),
        ):
            _handle_payment_failed(order, {}, "viva_txn_2")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING
