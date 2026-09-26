"""A confirmed charge on a CANCELED order is booked and escalated.

Since 2026-09-18 ``Order.save`` settles an unpaid order's payment to
CANCELED on the cancel transition, so a canceled order ALWAYS carries a
settled ``payment_status``. Every payment-success handler checked that
settled state first and dropped the event with a WARNING — which made
their "payment after cancel" branches unreachable for exactly the orders
they exist for: the shopper who pays on a stale tab after the 24-hour
auto-cancel. The money was taken, the order stayed dead and nobody was
told.

The fixtures below are the REAL shape (CANCELED / CANCELED), which the
older tests never used (they built CANCELED / PENDING, a combination
``save`` no longer produces).
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from djstripe.models import Event

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.models.history import OrderHistory
from order.services import OrderService
from order.signals.handlers import handle_stripe_checkout_completed
from order.views.viva_webhook import _handle_payment_created

pytestmark = pytest.mark.django_db


def _canceled_order(**extra):
    return OrderFactory(
        status=OrderStatus.CANCELED,
        payment_status=PaymentStatus.CANCELED,
        num_order_items=0,
        **extra,
    )


def _notes(order):
    return [
        h.new_value["note"]
        for h in OrderHistory.objects.filter(order=order, change_type="NOTE")
    ]


class TestVivaChargeOnACanceledOrder:
    @staticmethod
    def _verified():
        return patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(PaymentStatus.COMPLETED, {"order_code": "OC9"}),
        )

    def test_books_the_money_alerts_and_ships_nothing(self):
        order = _canceled_order(metadata={"viva_order_codes": ["OC9"]})

        with (
            self._verified(),
            patch("shipping.alerts.send_ops_alert") as alert,
            patch(
                "shipping.services.ShippingService.dispatch_create_shipment_task"
            ) as dispatch,
            patch(
                "order.views.viva_webhook.dispatch_on_commit"
            ) as confirmation,
        ):
            outcome = _handle_payment_created(
                order, {"StatusId": "F"}, "viva_txn_9"
            )

        assert outcome is None  # processed, not skipped
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert order.payment_status == PaymentStatus.COMPLETED
        assert order.payment_id == "viva_txn_9"
        assert order.payment_method == "viva_wallet"
        record = order.metadata["payment_after_cancel"]
        assert record["payment_id"] == "viva_txn_9"
        assert record["payment_method"] == "viva_wallet"
        assert order.metadata["viva_transaction_id"] == "viva_txn_9"
        alert.assert_called_once()
        assert str(order.id) in alert.call_args.kwargs["subject"]
        assert any("after the order was canceled" in n for n in _notes(order))
        dispatch.assert_not_called()
        # No "we received your order" email for a canceled order.
        confirmation.assert_not_called()

    def test_a_redelivered_event_does_not_alert_twice(self):
        order = _canceled_order(metadata={"viva_order_codes": ["OC9"]})

        with self._verified(), patch("shipping.alerts.send_ops_alert") as alert:
            _handle_payment_created(order, {"StatusId": "F"}, "viva_txn_9")
            order.refresh_from_db()
            _handle_payment_created(order, {"StatusId": "F"}, "viva_txn_9")

        alert.assert_called_once()

    def test_a_refunded_canceled_order_still_ignores_a_stale_success(self):
        """The money already went back: a late success event is stale and
        must not flip the order to paid or page anybody."""
        order = OrderFactory(
            status=OrderStatus.CANCELED,
            payment_status=PaymentStatus.REFUNDED,
            num_order_items=0,
            metadata={"viva_order_codes": ["OC9"]},
        )

        with self._verified(), patch("shipping.alerts.send_ops_alert") as alert:
            _handle_payment_created(order, {"StatusId": "F"}, "viva_txn_9")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        assert "payment_after_cancel" not in order.metadata
        alert.assert_not_called()


class TestStripeChargeOnACanceledOrder:
    def test_payment_intent_success_is_booked_and_alerted(self):
        order = _canceled_order(payment_id="pi_after_cancel")

        with (
            patch("shipping.alerts.send_ops_alert") as alert,
            patch(
                "order.services.OrderService._dispatch_shipment_creation_task"
            ) as dispatch,
        ):
            OrderService.handle_payment_succeeded("pi_after_cancel")

        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert order.payment_status == PaymentStatus.COMPLETED
        assert order.metadata["payment_after_cancel"]["payment_id"] == (
            "pi_after_cancel"
        )
        alert.assert_called_once()
        dispatch.assert_not_called()

    def test_checkout_session_is_booked_and_alerted(self):
        order = _canceled_order(metadata={})
        event = Mock(spec=Event)
        event.id = f"evt_cs_{order.id}"
        event.type = "checkout.session.completed"
        event.data = {
            "object": {
                "id": "cs_after_cancel",
                "payment_intent": "pi_cs_after_cancel",
                "payment_status": "paid",
                "metadata": {"order_id": str(order.id)},
            }
        }

        with (
            patch("shipping.alerts.send_ops_alert") as alert,
            patch(
                "shipping.services.ShippingService.dispatch_create_shipment_task"
            ) as dispatch,
        ):
            handle_stripe_checkout_completed(sender=None, event=event)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert order.metadata["payment_after_cancel"]["payment_id"] == (
            "pi_cs_after_cancel"
        )
        assert order.metadata[f"webhook_processed_evt_cs_{order.id}"] is True
        alert.assert_called_once()
        dispatch.assert_not_called()
