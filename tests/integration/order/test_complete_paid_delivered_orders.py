"""The hourly sweep closes DELIVERED orders that are paid.

``maybe_advance_to_completed`` runs inline at two moments only (carrier
DELIVERED, COD reconcile). A payment entered by hand, a webhook after
delivery, or an inline attempt that failed once leaves the order at
DELIVERED for good — prod orders 252, 253, 73 and 92 on 2026-09-20.
"""

from __future__ import annotations

import pytest

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.tasks import complete_paid_delivered_orders

pytestmark = pytest.mark.django_db


def test_paid_delivered_orders_are_completed_silently():
    paid = OrderFactory(
        status=OrderStatus.DELIVERED, payment_status=PaymentStatus.COMPLETED
    )
    cod_waiting = OrderFactory(
        status=OrderStatus.DELIVERED, payment_status=PaymentStatus.PENDING
    )
    shipped = OrderFactory(
        status=OrderStatus.SHIPPED, payment_status=PaymentStatus.COMPLETED
    )

    result = complete_paid_delivered_orders()

    assert result == {"candidates": 1, "advanced": 1}
    paid.refresh_from_db()
    assert paid.status == OrderStatus.COMPLETED
    # Automatic COMPLETED never emails or toasts the customer.
    assert paid.metadata.get("suppress_status_ws_COMPLETED") is True
    assert paid.metadata.get("status_update_email_sent_COMPLETED") is True
    assert Order.objects.get(pk=cod_waiting.pk).status == OrderStatus.DELIVERED
    assert Order.objects.get(pk=shipped.pk).status == OrderStatus.SHIPPED


def test_nothing_to_do_is_a_quiet_noop():
    assert complete_paid_delivered_orders() == {"candidates": 0, "advanced": 0}
