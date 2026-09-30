from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.utils import timezone

from order.enum.status import OrderStatus
from order.exceptions import InvalidStatusTransitionError
from order.factories import OrderFactory
from order.services import OrderService

# Define the allowed transitions from the OrderService
ALLOWED_TRANSITIONS = {
    OrderStatus.PENDING: [
        OrderStatus.PROCESSING,
        OrderStatus.CANCELED,
    ],
    OrderStatus.PROCESSING: [
        OrderStatus.SHIPPED,
        OrderStatus.CANCELED,
    ],
    OrderStatus.SHIPPED: [
        OrderStatus.DELIVERED,
        OrderStatus.RETURNED,
    ],
    OrderStatus.DELIVERED: [
        OrderStatus.COMPLETED,
        OrderStatus.RETURNED,
    ],
    OrderStatus.CANCELED: [],
    OrderStatus.COMPLETED: [],
    OrderStatus.RETURNED: [OrderStatus.REFUNDED],
    OrderStatus.REFUNDED: [],
}


def generate_all_transition_pairs():
    """
    Generate all possible (current_status, new_status) pairs for testing.

    Returns list of tuples: (current_status, new_status, should_succeed)
    """
    all_statuses = [
        OrderStatus.PENDING,
        OrderStatus.PROCESSING,
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
        OrderStatus.COMPLETED,
        OrderStatus.CANCELED,
        OrderStatus.RETURNED,
        OrderStatus.REFUNDED,
    ]

    pairs = []
    for current_status in all_statuses:
        allowed = ALLOWED_TRANSITIONS.get(current_status, [])
        for new_status in all_statuses:
            if current_status == new_status:
                continue  # Skip same-status transitions
            should_succeed = new_status in allowed
            pairs.append((current_status, new_status, should_succeed))

    return pairs


@pytest.mark.django_db
@pytest.mark.parametrize(
    "current_status,new_status,should_succeed", generate_all_transition_pairs()
)
def test_status_transition_follows_the_state_machine(
    current_status, new_status, should_succeed
):
    """A transition succeeds iff the table allows it. A successful one
    stamps ``status_updated_at`` and emits ``order_status_changed``
    exactly once; a refused one leaves the row untouched."""
    from order.signals import order_status_changed

    stale = timezone.now() - timedelta(minutes=5)
    order = OrderFactory(
        status=current_status, status_updated_at=stale, num_order_items=0
    )
    receiver = Mock()
    order_status_changed.connect(receiver)
    try:
        if should_succeed:
            before = timezone.now()
            OrderService.update_order_status(order, new_status)
            order.refresh_from_db()
            assert order.status == new_status
            assert before <= order.status_updated_at <= timezone.now()
            receiver.assert_called_once()
            kwargs = receiver.call_args.kwargs
            assert kwargs["order"] == order
            assert kwargs["old_status"] == current_status
            assert kwargs["new_status"] == new_status
        else:
            with pytest.raises(InvalidStatusTransitionError) as exc_info:
                OrderService.update_order_status(order, new_status)
            assert exc_info.value.current_status == current_status
            assert exc_info.value.new_status == new_status
            order.refresh_from_db()
            assert order.status == current_status
            assert order.status_updated_at == stale
            receiver.assert_not_called()
    finally:
        order_status_changed.disconnect(receiver)


# ---------------------------------------------------------------------------
# PR #2 G — DELIVERED -> COMPLETED auto-advance
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_maybe_advance_to_completed_paid_delivered_order_advances():
    """A DELIVERED order with payment_status=COMPLETED auto-advances."""
    from order.enum.status import PaymentStatus

    order = OrderFactory(
        status=OrderStatus.DELIVERED,
        payment_status=PaymentStatus.COMPLETED,
        num_order_items=0,
    )
    OrderService.maybe_advance_to_completed(order)
    order.refresh_from_db()
    assert order.status == OrderStatus.COMPLETED


@pytest.mark.django_db
def test_maybe_advance_to_completed_unpaid_delivered_order_stays():
    """A DELIVERED order with payment_status=PENDING (COD pre-reconcile)
    stays at DELIVERED — must wait for the payout flip."""
    from order.enum.status import PaymentStatus

    order = OrderFactory(
        status=OrderStatus.DELIVERED,
        payment_status=PaymentStatus.PENDING,
        num_order_items=0,
    )
    OrderService.maybe_advance_to_completed(order)
    order.refresh_from_db()
    assert order.status == OrderStatus.DELIVERED


@pytest.mark.django_db
@pytest.mark.parametrize(
    "current",
    [
        OrderStatus.PENDING,
        OrderStatus.PROCESSING,
        OrderStatus.SHIPPED,
        OrderStatus.COMPLETED,
        OrderStatus.CANCELED,
    ],
)
def test_maybe_advance_to_completed_non_delivered_order_no_op(current):
    """Non-DELIVERED orders are a no-op even when paid."""
    from order.enum.status import PaymentStatus

    order = OrderFactory(
        status=current,
        payment_status=PaymentStatus.COMPLETED,
        num_order_items=0,
    )
    OrderService.maybe_advance_to_completed(order)
    order.refresh_from_db()
    assert order.status == current
