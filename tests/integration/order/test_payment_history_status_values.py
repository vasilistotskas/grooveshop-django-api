"""Payment history rows record ``PaymentStatus`` values, not lowercase
literals (``order/signals/handlers.py``).

``Order.payment_status`` stores the upper-case enum value, so a history
row written as ``"completed"`` / ``"failed"`` matched no status and read
as a different vocabulary from every other row.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from djstripe.models import Event

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.models import OrderHistory
from order.services import PaymentEventOutcome
from order.signals.handlers import (
    handle_stripe_payment_failed,
    handle_stripe_payment_succeeded,
)

pytestmark = pytest.mark.django_db


def _event(event_id: str, payment_intent_id: str) -> Mock:
    event = Mock(spec=Event)
    event.id = event_id
    event.data = {"object": {"id": payment_intent_id}}
    return event


def _pending_order(payment_intent_id: str):
    return OrderFactory(
        num_order_items=0,
        payment_id=payment_intent_id,
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
    )


def _payment_entry(order) -> OrderHistory:
    return OrderHistory.objects.get(order=order, change_type="PAYMENT")


def test_payment_succeeded_history_uses_enum_values():
    order = _pending_order("pi_hist_ok")

    with (
        patch(
            "order.signals.handlers.OrderService.handle_payment_succeeded",
            # 3-D Secure: the intent was PROCESSING, not PENDING, and the
            # row must say so rather than assume where it came from.
            return_value=PaymentEventOutcome(
                order, PaymentStatus.PROCESSING, applied=True
            ),
        ),
        patch(
            "order.signals.handlers.send_order_confirmation_email.apply_async"
        ),
    ):
        handle_stripe_payment_succeeded(
            sender=None, event=_event("evt_hist_ok", "pi_hist_ok")
        )

    entry = _payment_entry(order)
    assert entry.previous_value == {
        "payment_status": PaymentStatus.PROCESSING.value
    }
    assert entry.new_value == {
        "payment_status": PaymentStatus.COMPLETED.value,
        "payment_id": "pi_hist_ok",
    }


def test_payment_failed_history_uses_enum_values():
    order = _pending_order("pi_hist_fail")

    with (
        patch(
            "order.signals.handlers.OrderService.handle_payment_failed",
            return_value=PaymentEventOutcome(
                order, PaymentStatus.PENDING, applied=True
            ),
        ),
        patch("order.signals.handlers.send_payment_failed_email.apply_async"),
    ):
        handle_stripe_payment_failed(
            sender=None, event=_event("evt_hist_fail", "pi_hist_fail")
        )

    entry = _payment_entry(order)
    assert entry.previous_value == {
        "payment_status": PaymentStatus.PENDING.value
    }
    assert entry.new_value == {
        "payment_status": PaymentStatus.FAILED.value,
        "payment_id": "pi_hist_fail",
    }


class TestVivaWebhookHistory:
    """``order/views/viva_webhook.py`` writes the same rows for Viva."""

    @staticmethod
    def _history(order):
        return OrderHistory.objects.filter(
            order=order, change_type="PAYMENT"
        ).latest("id")

    def test_payment_created(self):
        from order.views.viva_webhook import _handle_payment_created

        order = _pending_order("viva_hist_created")
        order.metadata = {"viva_order_codes": ["OC1"]}
        order.save(update_fields=["metadata"])
        with (
            patch(
                "order.views.viva_webhook._verify_transaction",
                return_value=(PaymentStatus.COMPLETED, {"order_code": "OC1"}),
            ),
            patch("order.views.viva_webhook.dispatch_on_commit"),
            patch(
                "shipping.services.ShippingService.dispatch_create_shipment_task"
            ),
        ):
            _handle_payment_created(order, {"StatusId": "F"}, "viva_txn_c")

        assert (
            self._history(order).new_value["payment_status"]
            == PaymentStatus.COMPLETED
        )

    @pytest.mark.parametrize(
        ("handler", "verified", "expected"),
        [
            (
                "_handle_payment_failed",
                PaymentStatus.FAILED,
                PaymentStatus.FAILED,
            ),
            (
                "_handle_reversal_created",
                PaymentStatus.REFUNDED,
                PaymentStatus.REFUNDED,
            ),
        ],
    )
    def test_terminal_events(self, handler, verified, expected):
        from order.views import viva_webhook

        order = _pending_order(f"viva_hist_{handler}")
        with (
            patch.object(
                viva_webhook,
                "_verify_viva_terminal_transaction",
                return_value=verified,
            ),
            patch.object(viva_webhook, "dispatch_on_commit"),
        ):
            getattr(viva_webhook, handler)(order, {}, "viva_txn_t")

        assert self._history(order).new_value["payment_status"] == expected
