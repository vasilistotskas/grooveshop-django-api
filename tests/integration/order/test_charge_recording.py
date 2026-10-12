"""Every place a provider confirms a charge writes a ``CHARGE`` row.

The row is what a later refund is made against and what ``payment_status``
after a refund is derived from, so each confirming path has to leave one,
under the provider's own id (a repeat is absorbed by the unique
constraint), whatever state the order is in: the money has moved either
way.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from djmoney.money import Money
from djstripe.models import Event

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.services import OrderService
from order.signals.handlers import (
    handle_stripe_checkout_completed,
    handle_stripe_payment_succeeded,
)
from order.views.viva_webhook import _handle_payment_created
from tests.utils.payments import CAPTURED

pytestmark = pytest.mark.django_db

CHARGE = PaymentTransactionKind.CHARGE


def _charges(order):
    return list(order.payment_transactions.filter(kind=CHARGE))


def _payment_intent_event(payment_intent_id, *, amount_received=5000):
    event = Mock(spec=Event)
    event.id = f"evt_{payment_intent_id}"
    event.type = "payment_intent.succeeded"
    event.data = {
        "object": {
            "id": payment_intent_id,
            "amount": 9999,
            "amount_received": amount_received,
            "currency": "eur",
        }
    }
    return event


def _checkout_event(order, payment_intent_id, *, amount_total=5000):
    event = Mock(spec=Event)
    event.id = f"evt_cs_{order.id}"
    event.type = "checkout.session.completed"
    event.data = {
        "object": {
            "id": "cs_1",
            "payment_intent": payment_intent_id,
            "payment_status": "paid",
            "amount_total": amount_total,
            "currency": "eur",
            "metadata": {"order_id": str(order.id)},
        }
    }
    return event


class TestStripePaymentIntentSucceeded:
    def test_the_amount_stripe_collected_is_recorded(self):
        order = OrderFactory(
            num_order_items=0,
            payment_id="pi_rec_1",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )

        handle_stripe_payment_succeeded(
            sender=None,
            # ``amount`` is what was asked for; ``amount_received`` is what
            # Stripe collected, and the one the ledger takes.
            event=_payment_intent_event("pi_rec_1", amount_received=4700),
        )

        [charge] = _charges(order)
        assert charge.provider == "stripe"
        assert charge.provider_reference == "pi_rec_1"
        assert charge.amount == Money(Decimal("47.00"), "EUR")

    def test_a_repeat_is_absorbed_by_the_constraint(self):
        order = OrderFactory(
            num_order_items=0,
            payment_id="pi_rec_2",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )

        for _ in range(2):
            OrderService.handle_payment_succeeded("pi_rec_2", captured=CAPTURED)

        assert len(_charges(order)) == 1

    @pytest.mark.parametrize(
        "payment_status",
        [
            PaymentStatus.COMPLETED,
            PaymentStatus.REFUNDED,
            PaymentStatus.PARTIALLY_REFUNDED,
            PaymentStatus.CANCELED,
        ],
    )
    def test_the_money_is_recorded_even_when_the_order_ignores_the_event(
        self, payment_status
    ):
        order = OrderFactory(
            num_order_items=0,
            payment_id="pi_rec_3",
            status=OrderStatus.DELIVERED,
            payment_status=payment_status,
        )

        OrderService.handle_payment_succeeded("pi_rec_3", captured=CAPTURED)

        assert [c.provider_reference for c in _charges(order)] == ["pi_rec_3"]

    def test_a_charge_on_a_canceled_order_is_recorded_so_it_can_be_refunded(
        self,
    ):
        order = OrderFactory(
            num_order_items=0,
            payment_id="pi_rec_4",
            status=OrderStatus.CANCELED,
            payment_status=PaymentStatus.CANCELED,
        )

        with patch("shipping.alerts.send_ops_alert"):
            OrderService.handle_payment_succeeded("pi_rec_4", captured=CAPTURED)

        [charge] = _charges(order)
        assert charge.amount == CAPTURED

    def test_an_event_without_the_amount_is_dropped_not_half_applied(self):
        order = OrderFactory(
            num_order_items=0,
            payment_id="pi_rec_5",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        event = _payment_intent_event("pi_rec_5")
        del event.data["object"]["amount_received"]

        handle_stripe_payment_succeeded(sender=None, event=event)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING
        assert _charges(order) == []


class TestStripeCheckoutSessionCompleted:
    def test_the_session_total_is_recorded_under_the_payment_intent(self):
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            metadata={},
        )

        with patch(
            "shipping.services.ShippingService.dispatch_create_shipment_task"
        ):
            handle_stripe_checkout_completed(
                sender=None,
                event=_checkout_event(order, "pi_cs_rec", amount_total=6250),
            )

        [charge] = _charges(order)
        assert charge.provider == "stripe"
        assert charge.provider_reference == "pi_cs_rec"
        assert charge.amount == Money(Decimal("62.50"), "EUR")

    def test_the_payment_intent_event_that_follows_records_nothing_more(self):
        """Stripe sends both for one hosted payment, under the same
        PaymentIntent id: one charge."""
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            metadata={},
        )

        with patch(
            "shipping.services.ShippingService.dispatch_create_shipment_task"
        ):
            handle_stripe_checkout_completed(
                sender=None,
                event=_checkout_event(order, "pi_cs_both", amount_total=5000),
            )
            handle_stripe_payment_succeeded(
                sender=None, event=_payment_intent_event("pi_cs_both")
            )

        assert len(_charges(order)) == 1


class TestVivaPaymentCreated:
    @staticmethod
    def _verified(amount, order_code="OC1"):
        return patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(
                PaymentStatus.COMPLETED,
                {"order_code": order_code, "amount": amount, "currency": "EUR"},
            ),
        )

    def _order(self, **kwargs):
        return OrderFactory(
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            metadata={"viva_order_codes": ["OC1", "OC2"]},
            **kwargs,
        )

    def test_the_verified_amount_is_recorded_under_the_transaction_id(self):
        order = self._order()
        # What the order owes is what Viva verified (within one cent).
        amount = float(order.calculate_order_total_amount().amount)

        with (
            self._verified(amount),
            patch("order.views.viva_webhook.dispatch_on_commit"),
            patch(
                "shipping.services.ShippingService.dispatch_create_shipment_task"
            ),
        ):
            _handle_payment_created(order, {"StatusId": "F"}, "viva_txn_a")

        [charge] = _charges(order)
        assert charge.provider == "viva_wallet"
        assert charge.provider_reference == "viva_txn_a"
        assert charge.amount.amount == Decimal(str(amount))

    def test_a_second_payment_under_another_code_is_a_second_charge(self):
        order = self._order()
        amount = float(order.calculate_order_total_amount().amount)

        with (
            self._verified(amount, "OC1"),
            patch("order.views.viva_webhook.dispatch_on_commit"),
            patch(
                "shipping.services.ShippingService.dispatch_create_shipment_task"
            ),
        ):
            _handle_payment_created(order, {"StatusId": "F"}, "viva_txn_a")
            order.refresh_from_db()
        with (
            self._verified(amount, "OC2"),
            patch("order.views.viva_webhook.dispatch_on_commit"),
        ):
            _handle_payment_created(order, {"StatusId": "F"}, "viva_txn_b")

        assert sorted(c.provider_reference for c in _charges(order)) == [
            "viva_txn_a",
            "viva_txn_b",
        ]

    def test_a_repeat_is_absorbed_by_the_constraint(self):
        order = self._order()
        amount = float(order.calculate_order_total_amount().amount)

        with (
            self._verified(amount),
            patch("order.views.viva_webhook.dispatch_on_commit"),
            patch(
                "shipping.services.ShippingService.dispatch_create_shipment_task"
            ),
        ):
            _handle_payment_created(order, {"StatusId": "F"}, "viva_txn_a")
            order.refresh_from_db()
            _handle_payment_created(order, {"StatusId": "F"}, "viva_txn_a")

        assert len(_charges(order)) == 1

    def test_an_amount_that_does_not_match_the_order_is_not_a_charge(self):
        """The payment is refused (and flagged for staff), not booked."""
        order = self._order()
        amount = float(order.calculate_order_total_amount().amount) + 5

        with self._verified(amount):
            _handle_payment_created(order, {"StatusId": "F"}, "viva_txn_a")

        assert _charges(order) == []
