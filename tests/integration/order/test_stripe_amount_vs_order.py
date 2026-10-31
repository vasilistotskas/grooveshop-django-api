"""Stripe's collected amount is compared with what the order is for.

The success handlers used to mark an order paid on the event's say-so: an
intent for EUR 1 could settle a EUR 130 order. A charge that does not match
is booked on the ledger (the money moved) but does not pay the order; the
order is flagged, never auto-canceled, and staff are paged.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from djmoney.money import Money
from djstripe.models import Event

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import AMOUNT_MISMATCH_FLAG
from order.models.payment_transaction import PaymentTransaction
from order.services import OrderService
from order.signals.handlers import handle_stripe_checkout_completed

pytestmark = pytest.mark.django_db

PAYMENT_ID = "pi_amount_check"


@pytest.fixture
def order():
    return OrderFactory(
        num_order_items=2,
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        payment_id=PAYMENT_ID,
        metadata={},
    )


@pytest.fixture
def alerts():
    with patch("emails.send.send_email") as alert:
        yield alert


class TestPaymentIntentSucceeded:
    def test_the_exact_total_pays_the_order(self, order):
        owed = order.calculate_order_total_amount()

        OrderService.handle_payment_succeeded(PAYMENT_ID, captured=owed)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert order.status == OrderStatus.PROCESSING

    def test_a_smaller_amount_does_not_pay_the_order(self, order, alerts):
        owed = order.calculate_order_total_amount()

        outcome = OrderService.handle_payment_succeeded(
            PAYMENT_ID, captured=Money(1, owed.currency)
        )

        order.refresh_from_db()
        assert outcome is not None
        assert not outcome.applied
        assert order.payment_status == PaymentStatus.PENDING
        assert order.status == OrderStatus.PENDING
        assert AMOUNT_MISMATCH_FLAG in order.metadata
        assert PaymentTransaction.objects.filter(
            order=order, provider_reference=PAYMENT_ID
        ).exists(), "the money moved: the charge is on the ledger"
        assert alerts.call_args.args[0] == "ops.payment_mismatch"

    def test_another_currency_does_not_pay_the_order(self, order, alerts):
        owed = order.calculate_order_total_amount()

        OrderService.handle_payment_succeeded(
            PAYMENT_ID, captured=Money(owed.amount, "USD")
        )

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING
        assert AMOUNT_MISMATCH_FLAG in order.metadata


class TestCheckoutSessionCompleted:
    def _event(self, order, amount_total):
        event = Mock(spec=Event)
        event.id = "evt_cs_amount"
        event.type = "checkout.session.completed"
        event.data = {
            "object": {
                "id": "cs_amount",
                "payment_intent": PAYMENT_ID,
                "payment_status": "paid",
                "amount_total": amount_total,
                "currency": "eur",
                "metadata": {"order_id": str(order.id)},
            }
        }
        return event

    def test_a_session_paid_for_another_amount_settles_nothing(
        self, order, alerts
    ):
        handle_stripe_checkout_completed(
            sender=None, event=self._event(order, 100)
        )

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING
        assert order.status == OrderStatus.PENDING
        assert AMOUNT_MISMATCH_FLAG in order.metadata
        alerts.assert_called_once()

    def test_a_session_paid_for_the_total_settles_the_order(self, order):
        owed = order.calculate_order_total_amount()

        with patch(
            "shipping.services.ShippingService.dispatch_create_shipment_task"
        ):
            handle_stripe_checkout_completed(
                sender=None,
                event=self._event(order, round(owed.amount * 100)),
            )

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
