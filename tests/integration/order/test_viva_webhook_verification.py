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
    _handle_payment_created,
    _handle_payment_failed,
    _handle_reversal_created,
)


@pytest.mark.django_db
class TestVivaReversalVerification:
    def _order(self):
        # ``viva_order_codes`` carries the codes this order actually
        # issued. Viva's guidance is to confirm a result with the
        # COMBINATION of OrderCode and TransactionId, so a verified
        # transaction that names a code the order never issued is exactly
        # what the handler must refuse — the fixture has to name a real
        # one for the happy path to be reachable.
        return OrderFactory(
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
            payment_id="viva_txn_1",
            metadata={"viva_order_codes": ["OC1"]},
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
        # See the note above: the order must have issued the code its
        # verified transaction reports.
        return OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id="viva_txn_2",
            metadata={"viva_order_codes": ["OC2"]},
        )

    def test_failed_flips_to_failed_when_verified(self):
        order = self._order()
        with (
            patch(
                "order.views.viva_webhook._verify_transaction",
                return_value=(PaymentStatus.FAILED, {"order_code": "OC2"}),
            ),
            patch(
                "order.views.viva_webhook.send_payment_failed_email.apply_async"
            ) as mock_email,
        ):
            _handle_payment_failed(order, {}, "viva_txn_2")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.FAILED
        # Dispatched with the schema captured at lambda-build time so
        # the worker enters the owning tenant's schema.
        mock_email.assert_called_once()
        assert mock_email.call_args.kwargs["args"] == [order.id]
        assert "_schema_name" in mock_email.call_args.kwargs["headers"]

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


@pytest.mark.django_db
class TestVivaPaymentCreatedVerification:
    """The payment-created (1796) handler must treat a Retrieve-
    Transaction ERROR as "unavailable" (raise → 500 → retry), not as a
    confirmed non-completion. This is both the fix for a latent
    single-tenant bug (a transient Viva error was silently acked) and
    the mechanism that lets a webhook whose transaction is not in THIS
    tenant's Viva account fall through to the next candidate tenant.
    """

    def _order(self):
        return OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            num_order_items=1,
            metadata={},
        )

    def test_raises_when_verification_reports_viva_error(self):
        """A 404 because the transaction is not in this tenant's Viva
        account maps to (FAILED, {viva_error: True}) — must raise, not
        skip."""
        order = self._order()
        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(PaymentStatus.FAILED, {"viva_error": True}),
        ):
            with pytest.raises(RuntimeError):
                _handle_payment_created(order, {"StatusId": "F"}, "foreign_txn")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING

    def test_raises_when_verification_reports_error(self):
        order = self._order()
        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(PaymentStatus.FAILED, {"error": "timeout"}),
        ):
            with pytest.raises(RuntimeError):
                _handle_payment_created(order, {"StatusId": "F"}, "txn")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING

    def test_clean_non_completed_status_skips_without_raising(self):
        """A genuine, error-free non-COMPLETED status (a real pending
        transaction in this account) is a legitimate skip — not a
        verification failure — so it must NOT raise."""
        order = self._order()
        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(
                PaymentStatus.PENDING,
                {"order_code": "OC", "amount": None},
            ),
        ):
            # Does not raise.
            _handle_payment_created(order, {"StatusId": "F"}, "txn")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING
