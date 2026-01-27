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
def test_property_15_state_transitions_follow_state_machine(
    current_status, new_status, should_succeed
):
    """
    For any order status transition attempt, the transition SHALL succeed
    if and only if it exists in the allowed_transitions dictionary for the current status.
    """
    # Create order with current status
    order = OrderFactory(status=current_status)
    service = OrderService()

    if should_succeed:
        # Allowed transition should succeed
        service.update_order_status(order, new_status)
        order.refresh_from_db()
        assert order.status == new_status
    else:
        # Disallowed transition should raise InvalidStatusTransitionError
        with pytest.raises(InvalidStatusTransitionError):
            service.update_order_status(order, new_status)
        order.refresh_from_db()
        assert order.status == current_status  # Status unchanged


@pytest.mark.django_db
@pytest.mark.parametrize(
    "current_status,new_status",
    [
        (OrderStatus.PENDING, OrderStatus.PROCESSING),
        (OrderStatus.PENDING, OrderStatus.CANCELED),
        (OrderStatus.PROCESSING, OrderStatus.SHIPPED),
        (OrderStatus.PROCESSING, OrderStatus.CANCELED),
        (OrderStatus.SHIPPED, OrderStatus.DELIVERED),
        (OrderStatus.SHIPPED, OrderStatus.RETURNED),
        (OrderStatus.DELIVERED, OrderStatus.COMPLETED),
        (OrderStatus.DELIVERED, OrderStatus.RETURNED),
        (OrderStatus.RETURNED, OrderStatus.REFUNDED),
    ],
)
def test_property_16_status_changes_update_timestamp(
    current_status, new_status
):
    """
    For any order status transition, the Order.status_updated_at field
    SHALL be updated to the current timestamp.
    """
    # Create order with current status and ensure status_updated_at is set
    initial_timestamp = timezone.now() - timedelta(minutes=5)
    order = OrderFactory(
        status=current_status, status_updated_at=initial_timestamp
    )
    old_timestamp = order.status_updated_at

    # Wait a small amount to ensure timestamp difference
    import time

    time.sleep(0.01)

    # Update status
    service = OrderService()
    before_update = timezone.now()
    service.update_order_status(order, new_status)
    after_update = timezone.now()

    # Verify timestamp updated
    order.refresh_from_db()
    assert order.status_updated_at > old_timestamp
    assert before_update <= order.status_updated_at <= after_update


@pytest.mark.django_db
@pytest.mark.parametrize(
    "current_status,new_status",
    [
        (OrderStatus.PENDING, OrderStatus.PROCESSING),
        (OrderStatus.PENDING, OrderStatus.CANCELED),
        (OrderStatus.PROCESSING, OrderStatus.SHIPPED),
        (OrderStatus.PROCESSING, OrderStatus.CANCELED),
        (OrderStatus.SHIPPED, OrderStatus.DELIVERED),
        (OrderStatus.SHIPPED, OrderStatus.RETURNED),
        (OrderStatus.DELIVERED, OrderStatus.COMPLETED),
        (OrderStatus.DELIVERED, OrderStatus.RETURNED),
        (OrderStatus.RETURNED, OrderStatus.REFUNDED),
    ],
)
def test_property_17_status_changes_trigger_signals(current_status, new_status):
    """
    For any order status transition, the order_status_changed signal
    SHALL be emitted with the order, old_status, and new_status.
    """
    # Create order with current status
    order = OrderFactory(status=current_status)

    # Mock signal receiver
    mock_receiver = Mock()

    # Connect mock receiver to signal
    from order.signals import order_status_changed

    order_status_changed.connect(mock_receiver)

    try:
        # Update status
        service = OrderService()
        service.update_order_status(order, new_status)

        # Verify signal was called
        mock_receiver.assert_called_once()
        call_kwargs = mock_receiver.call_args[1]
        assert call_kwargs["sender"] == order.__class__
        assert call_kwargs["order"] == order
        assert call_kwargs["old_status"] == current_status
        assert call_kwargs["new_status"] == new_status
    finally:
        # Disconnect mock receiver
        order_status_changed.disconnect(mock_receiver)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "current_status,backwards_status",
    [
        (OrderStatus.PROCESSING, OrderStatus.PENDING),
        (OrderStatus.SHIPPED, OrderStatus.PENDING),
        (OrderStatus.SHIPPED, OrderStatus.PROCESSING),
        (OrderStatus.DELIVERED, OrderStatus.PENDING),
        (OrderStatus.DELIVERED, OrderStatus.PROCESSING),
        (OrderStatus.DELIVERED, OrderStatus.SHIPPED),
        (OrderStatus.COMPLETED, OrderStatus.PENDING),
        (OrderStatus.COMPLETED, OrderStatus.PROCESSING),
        (OrderStatus.COMPLETED, OrderStatus.SHIPPED),
        (OrderStatus.COMPLETED, OrderStatus.DELIVERED),
        (OrderStatus.REFUNDED, OrderStatus.PENDING),
        (OrderStatus.REFUNDED, OrderStatus.PROCESSING),
        (OrderStatus.REFUNDED, OrderStatus.SHIPPED),
        (OrderStatus.REFUNDED, OrderStatus.DELIVERED),
        (OrderStatus.REFUNDED, OrderStatus.COMPLETED),
        (OrderStatus.REFUNDED, OrderStatus.RETURNED),
    ],
)
def test_property_18_backwards_transitions_are_prevented(
    current_status, backwards_status
):
    """
    For any order, attempting to transition from a later stage to an
    earlier stage SHALL be rejected with InvalidStatusTransitionError.
    """
    # Create order with current status
    order = OrderFactory(status=current_status)
    service = OrderService()

    # Attempt backwards transition
    with pytest.raises(InvalidStatusTransitionError):
        service.update_order_status(order, backwards_status)

    # Verify status unchanged
    order.refresh_from_db()
    assert order.status == current_status
