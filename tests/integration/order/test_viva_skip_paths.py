"""A Viva 1796 that is NOT acted on must say so, and a charged order must
never be auto-cancelled.

Three separate holes, one theme: the webhook told Viva "processed" for
work it had refused to do.

- Every skip path returned ``None``, so the caller stamped the
  ``VivaWebhookEvent`` row ``processed``. The audit table claimed work
  that never happened, and the row permanently dedups the transaction.
- The amount guard refuses to mark an order paid when Viva's verified
  amount does not match the total — the usual cause being a shopper who
  pays on a stale checkout tab after the total moved. The money has still
  left the customer, and nothing recorded that.
- ``auto_cancel_stuck_pending_orders`` then closed the order a day later
  with ``refund_payment=False`` and emailed the customer that it was
  cancelled.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.models.viva_webhook_event import VivaWebhookEvent
from order.views.viva_webhook import (
    AMOUNT_MISMATCH_FLAG,
    _handle_payment_created,
)

pytestmark = pytest.mark.django_db


def _order(total="50.00"):
    order = OrderFactory(num_order_items=0)
    Order.objects.filter(pk=order.pk).update(
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        metadata={},
    )
    order.refresh_from_db()
    return order


def _verified(status, amount):
    return patch(
        "order.views.viva_webhook._verify_transaction",
        return_value=(status, {"amount": amount}),
    )


class TestSkipsAreReportedAsSkips:
    def test_amount_mismatch_reports_a_skip(self):
        order = _order()
        expected = order.calculate_order_total_amount().amount

        with _verified(PaymentStatus.COMPLETED, str(expected - Decimal("2"))):
            outcome = _handle_payment_created(
                order, {"StatusId": "F"}, "txn-mismatch"
            )

        assert outcome == VivaWebhookEvent.OUTCOME_SKIPPED
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING

    def test_a_non_completed_transaction_reports_a_skip(self):
        order = _order()

        with _verified(PaymentStatus.PENDING, "50.00"):
            outcome = _handle_payment_created(
                order, {"StatusId": "F"}, "txn-pending"
            )

        assert outcome == VivaWebhookEvent.OUTCOME_SKIPPED

    def test_an_unparseable_amount_does_not_escape(self):
        """``Decimal('abc')`` raises decimal.InvalidOperation, which is an
        ArithmeticError, not a ValueError — so the documented fallback
        never ran and the exception left the view as a 500 that Viva
        retries on a payload that will never parse."""
        order = _order()

        with _verified(PaymentStatus.COMPLETED, "not-a-number"):
            outcome = _handle_payment_created(
                order, {"StatusId": "F"}, "txn-garbage"
            )

        # Falls through to status-only verification and settles.
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert outcome != VivaWebhookEvent.OUTCOME_SKIPPED


class TestChargedOrderIsNotAutoCancelled:
    def test_the_mismatch_is_recorded_on_the_order(self):
        order = _order()
        expected = order.calculate_order_total_amount().amount

        with _verified(PaymentStatus.COMPLETED, str(expected - Decimal("2"))):
            _handle_payment_created(order, {"StatusId": "F"}, "txn-1")

        order.refresh_from_db()
        flag = order.metadata[AMOUNT_MISMATCH_FLAG]
        assert flag["transaction_id"] == "txn-1"
        assert Decimal(flag["expected_amount"]) == expected
        assert Decimal(flag["verified_amount"]) == expected - Decimal("2")

    def test_auto_cancel_leaves_a_charged_order_alone(self):
        from datetime import timedelta

        from django.utils import timezone

        from order.tasks import auto_cancel_stuck_pending_orders
        from pay_way.factories import PayWayFactory

        pay_way = PayWayFactory(is_online_payment=True)
        order = OrderFactory(num_order_items=0, pay_way=pay_way)
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            metadata={AMOUNT_MISMATCH_FLAG: {"transaction_id": "txn-1"}},
            created_at=timezone.now() - timedelta(days=3),
        )

        auto_cancel_stuck_pending_orders()

        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING, (
            "a charged order was auto-cancelled with refund_payment=False"
        )

    def test_an_ordinary_stale_order_is_still_cancelled(self):
        from datetime import timedelta

        from django.utils import timezone

        from order.tasks import auto_cancel_stuck_pending_orders
        from pay_way.factories import PayWayFactory

        pay_way = PayWayFactory(is_online_payment=True)
        order = OrderFactory(num_order_items=0, pay_way=pay_way)
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            metadata={},
            created_at=timezone.now() - timedelta(days=3),
        )

        auto_cancel_stuck_pending_orders()

        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
