"""Integration tests for PR #2 H + I.

H: Cancelling an order cascades to the courier voucher.
I: Stripe ``charge.refunded`` webhook flips Order.payment_status.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from djstripe.models import Event

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.services import OrderService
from order.signals.handlers import handle_stripe_charge_refunded


# ---------------------------------------------------------------------------
# I — charge.refunded webhook
# ---------------------------------------------------------------------------


def _build_charge_event(
    *,
    event_id: str,
    payment_intent_id: str,
    amount: int,
    amount_refunded: int,
    currency: str = "eur",
) -> Mock:
    event = Mock(spec=Event)
    event.id = event_id
    event.type = "charge.refunded"
    event.data = {
        "object": {
            "id": "ch_test_xxx",
            "payment_intent": payment_intent_id,
            "amount": amount,
            "amount_refunded": amount_refunded,
            "currency": currency,
            "refunded": amount_refunded >= amount > 0,
        }
    }
    return event


@pytest.mark.django_db
class TestChargeRefundedWebhook:
    def test_full_refund_flips_payment_status_to_refunded(self):
        order = OrderFactory(
            payment_id="pi_full_refund_1",
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.COMPLETED,
        )
        event = _build_charge_event(
            event_id="evt_full_refund_1",
            payment_intent_id="pi_full_refund_1",
            amount=5000,
            amount_refunded=5000,
        )
        handle_stripe_charge_refunded(sender=None, event=event)
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        assert order.metadata["refunds"][-1]["is_full_refund"] is True
        assert order.metadata["refunds"][-1]["amount_refunded"] == 5000

    def test_partial_refund_flips_payment_status_to_partially_refunded(self):
        order = OrderFactory(
            payment_id="pi_partial_refund_1",
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.COMPLETED,
        )
        event = _build_charge_event(
            event_id="evt_partial_refund_1",
            payment_intent_id="pi_partial_refund_1",
            amount=5000,
            amount_refunded=1500,
        )
        handle_stripe_charge_refunded(sender=None, event=event)
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        assert order.metadata["refunds"][-1]["is_full_refund"] is False
        assert order.metadata["refunds"][-1]["amount_refunded"] == 1500

    def test_redelivery_is_idempotent(self):
        order = OrderFactory(
            payment_id="pi_idempotent_1",
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.COMPLETED,
        )
        event = _build_charge_event(
            event_id="evt_idempotent_1",
            payment_intent_id="pi_idempotent_1",
            amount=5000,
            amount_refunded=5000,
        )
        handle_stripe_charge_refunded(sender=None, event=event)
        # Replay the same event — should not duplicate metadata entries
        handle_stripe_charge_refunded(sender=None, event=event)
        order.refresh_from_db()
        assert len(order.metadata["refunds"]) == 1

    def test_unknown_payment_intent_silently_warns(self):
        event = _build_charge_event(
            event_id="evt_unknown_1",
            payment_intent_id="pi_does_not_exist",
            amount=5000,
            amount_refunded=5000,
        )
        # Must not raise.
        handle_stripe_charge_refunded(sender=None, event=event)

    def test_event_without_payment_intent_id_no_ops(self):
        event = Mock(spec=Event)
        event.id = "evt_no_pi"
        event.type = "charge.refunded"
        event.data = {
            "object": {
                "id": "ch_no_pi",
                "payment_intent": "",
                "amount": 5000,
                "amount_refunded": 5000,
            }
        }
        handle_stripe_charge_refunded(sender=None, event=event)


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
        order = OrderFactory(
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.PENDING,
        )
        with patch(
            "shipping.services.ShippingService.cancel_shipment",
            side_effect=RuntimeError(
                "carrier rejected — already in pickup list"
            ),
        ):
            OrderService.cancel_order(order, reason="m", refund_payment=False)
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
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.PENDING,
        )
        OrderService.cancel_order(order, reason="m", refund_payment=False)
        order.refresh_from_db()
        cancellation = order.metadata.get("cancellation", {})
        shipment_cancel = cancellation.get("shipment_cancel", {})
        assert shipment_cancel.get("attempted") is True
        assert shipment_cancel.get("dispatched") is False
