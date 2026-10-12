"""Integration tests for PR #2 H + the refund concurrency guard.

H: Cancelling an order cascades to the courier voucher.
The ``charge.refunded`` webhook (PR #2 I) is covered in
``test_stripe_charge_refunded.py`` since the payment ledger.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from djmoney.money import Money

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import PaymentError
from order.factories import OrderFactory, PaymentTransactionFactory
from order.models.order import Order
from order.services import OrderService
from pay_way.factories import PayWayFactory

# ---------------------------------------------------------------------------
# H — order cancel cascades to courier voucher
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCancelOrderCascadesToShipment:
    """``OrderService.cancel_order`` calls ``ShippingService.cancel_shipment``.

    The cancel-shipment path itself is provider-specific (ACS guard
    rejects post-pickup-list, BoxNow has its own rules) — the
    cascade we're testing is that the call IS made and that exceptions
    do not stop the order cancel from completing.
    """

    def test_cascade_calls_cancel_shipment(self):
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.PENDING,
        )
        with patch(
            "shipping.services.ShippingService.cancel_shipment",
            return_value=True,
        ) as mock_cancel:
            OrderService.cancel_order(
                order, reason="cascade test", refund_payment=False
            )
        mock_cancel.assert_called_once()
        # First positional arg is the order, ``reason`` is keyword.
        called_order = mock_cancel.call_args.args[0]
        assert called_order.id == order.id
        assert "cascade test" in mock_cancel.call_args.kwargs.get("reason", "")

    def test_cascade_records_dispatched_status_in_metadata(self):
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.PENDING,
        )
        with patch(
            "shipping.services.ShippingService.cancel_shipment",
            return_value=True,
        ):
            OrderService.cancel_order(order, reason="m", refund_payment=False)
        order.refresh_from_db()
        cancellation = order.metadata.get("cancellation", {})
        shipment_cancel = cancellation.get("shipment_cancel", {})
        assert shipment_cancel.get("attempted") is True
        assert shipment_cancel.get("dispatched") is True

    def test_cascade_swallows_carrier_error_so_order_still_cancels(self):
        """A staff or system cancel (``customer_initiated=False``) is not
        bound by the courier: it cancels and records the refusal. A
        customer cancel is held to it (test_customer_cancel_asks_courier)."""
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.PENDING,
        )
        with patch(
            "shipping.services.ShippingService.cancel_shipment",
            side_effect=RuntimeError(
                "carrier rejected — already in pickup list"
            ),
        ):
            OrderService.cancel_order(
                order,
                reason="m",
                refund_payment=False,
                customer_initiated=False,
            )
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        cancellation = order.metadata.get("cancellation", {})
        shipment_cancel = cancellation.get("shipment_cancel", {})
        assert shipment_cancel.get("attempted") is True
        assert shipment_cancel.get("dispatched") is False
        assert "carrier rejected" in shipment_cancel.get("error", "")

    def test_cascade_no_op_when_no_carrier_attached(self):
        """Orders without a shipping_provider get ``dispatched=False`` —
        the cascade still records the attempt but ShippingService
        returns False on the missing-adapter no-op path."""
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.PENDING,
        )
        OrderService.cancel_order(order, reason="m", refund_payment=False)
        order.refresh_from_db()
        cancellation = order.metadata.get("cancellation", {})
        shipment_cancel = cancellation.get("shipment_cancel", {})
        assert shipment_cancel.get("attempted") is True
        assert shipment_cancel.get("dispatched") is False


@pytest.mark.django_db
class TestRefundOrderConcurrencyGuard:
    """refund_order must read what is left from the LOCKED ledger, so a
    concurrent refund that already committed can't be double-issued
    (G0280)."""

    def _stale_order(self):
        order = OrderFactory(
            status=OrderStatus.COMPLETED,
            payment_status=PaymentStatus.COMPLETED,
            payment_id="pi_refund_race",
            pay_way=PayWayFactory(),
            amount_due=Money("50.00", "EUR"),
            num_order_items=0,
        )
        charge = PaymentTransactionFactory(
            order=order,
            amount=Money("50.00", "EUR"),
            provider_reference="pi_refund_race",
        )
        return order, charge

    def test_a_refund_that_took_everything_leaves_nothing_to_refund(self):
        order, charge = self._stale_order()

        # A concurrent refund that already committed, WITHOUT touching the
        # caller's stale in-memory ``order`` (still COMPLETED).
        PaymentTransactionFactory(
            order=order,
            charge=charge,
            kind=PaymentTransactionKind.REFUND,
            amount=Money("50.00", "EUR"),
            provider_reference="re_race_1",
        )
        Order.objects.filter(pk=order.pk).update(
            payment_status=PaymentStatus.REFUNDED
        )

        with patch("order.payment.get_payment_provider") as mock_provider:
            with pytest.raises(PaymentError):
                OrderService.refund_order(order, reason="dup")

            # The locked ledger blocked it: no second provider refund.
            mock_provider.assert_not_called()

    def test_a_refund_that_took_part_shrinks_what_may_be_refunded(self):
        order, charge = self._stale_order()
        PaymentTransactionFactory(
            order=order,
            charge=charge,
            kind=PaymentTransactionKind.REFUND,
            amount=Money("30.00", "EUR"),
            provider_reference="re_race_1",
        )

        with patch("order.payment.get_payment_provider") as mock_provider:
            with pytest.raises(PaymentError, match=r"\(20\.00\)"):
                OrderService.refund_order(
                    order, amount=Money("30.00", "EUR"), reason="dup"
                )

            mock_provider.assert_not_called()
