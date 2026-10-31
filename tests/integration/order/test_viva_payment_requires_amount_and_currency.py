"""A Viva payment-created (1796) settles an order only when the verified
transaction states its amount AND currency and both are the order's.

Both checks used to be skipped when the field was missing, so a
transaction that reported neither settled the order on status alone.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import AMOUNT_MISMATCH_FLAG
from order.models.viva_webhook_event import VivaWebhookEvent
from order.views.viva_webhook import _handle_payment_created
from pay_way.factories import PayWayFactory

pytestmark = pytest.mark.django_db

ORDER_CODE = "7000000000000001"


@pytest.fixture
def order():
    return OrderFactory(
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        num_order_items=2,
        pay_way=PayWayFactory.create_online_payment("viva_wallet"),
        metadata={"viva_order_codes": [ORDER_CODE]},
    )


def _deliver(order, **overrides):
    owed = order.calculate_order_total_amount()
    data = {
        "amount": float(owed.amount),
        "currency": str(owed.currency),
        "order_code": ORDER_CODE,
        **overrides,
    }
    with patch(
        "order.views.viva_webhook._verify_transaction",
        return_value=(PaymentStatus.COMPLETED, data),
    ):
        return _handle_payment_created(order, {"StatusId": "F"}, "txn-1")


def test_a_transaction_that_states_both_settles_the_order(order):
    _deliver(order)

    order.refresh_from_db()
    assert order.payment_status == PaymentStatus.COMPLETED


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": None},
        {"amount": "not-a-number"},
        {"currency": None},
        {"currency": ""},
    ],
    ids=["no amount", "unreadable amount", "no currency", "blank currency"],
)
def test_a_transaction_missing_either_is_refused_and_flagged(order, overrides):
    with patch("emails.send.send_email") as alert:
        outcome = _deliver(order, **overrides)

    order.refresh_from_db()
    assert outcome == VivaWebhookEvent.OUTCOME_SKIPPED
    assert order.payment_status == PaymentStatus.PENDING
    assert AMOUNT_MISMATCH_FLAG in order.metadata, (
        "the customer was charged: the order is flagged, not auto-canceled"
    )
    alert.assert_called_once()
