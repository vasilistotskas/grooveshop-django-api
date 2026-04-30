"""Unit tests for the Phase 3 generic dispatch helpers.

Validates that ``ShippingService.create_shipment_row_for_order`` and
``ShippingService.dispatch_create_shipment_task`` route to the
right adapter via the registry — and gracefully no-op when the order
has no provider attached.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from order.factories.order import OrderFactory
from shipping.factories import ShippingProviderFactory
from shipping.services import ShippingService

pytestmark = pytest.mark.django_db


def _attach_provider(order, code):
    provider = ShippingProviderFactory(
        code=code,
        is_active=True,
        supports_home_delivery=True,
        supports_pickup_point=True,
    )
    order.shipping_provider = provider
    order.shipping_kind = "pickup_point"
    order.save(update_fields=["shipping_provider", "shipping_kind"])
    return provider


def test_create_shipment_row_dispatches_to_boxnow_carrier():
    """When the order has provider=boxnow, the BoxNow carrier's
    create_shipment_row hook is invoked with the right kind + payload."""
    order = OrderFactory()
    _attach_provider(order, "boxnow")

    with patch(
        "shipping_boxnow.carrier.BoxNowCarrier.create_shipment_row"
    ) as mock_hook:
        ShippingService.create_shipment_row_for_order(
            order,
            payload={"boxnow_locker_id": "TEST-1"},
            items=[],
        )

    assert mock_hook.called
    call_kwargs = mock_hook.call_args.kwargs
    assert call_kwargs["payload"]["boxnow_locker_id"] == "TEST-1"


def test_create_shipment_row_no_op_when_provider_unset():
    """Orders with no FK + no legacy method → adapter lookup returns
    None → service is a no-op (does not crash)."""
    order = OrderFactory()  # no shipping_provider set
    # Should not raise.
    ShippingService.create_shipment_row_for_order(order)


def test_dispatch_task_dispatches_to_boxnow_via_provider_fk():
    """Orders with shipping_provider=boxnow attached dispatch through
    the BoxNow carrier's Celery task hook."""
    order = OrderFactory()
    _attach_provider(order, "boxnow")

    with patch(
        "shipping_boxnow.carrier.BoxNowCarrier.dispatch_create_shipment_task"
    ) as mock_dispatch:
        ShippingService.dispatch_create_shipment_task(order)

    assert mock_dispatch.called


def test_dispatch_task_dispatches_to_acs_via_provider_fk():
    order = OrderFactory()
    _attach_provider(order, "acs")

    with patch(
        "shipping_acs.carrier.AcsCarrier.dispatch_create_shipment_task"
    ) as mock_dispatch:
        ShippingService.dispatch_create_shipment_task(order)

    assert mock_dispatch.called


def test_dispatch_task_no_op_for_orders_without_provider():
    """Orders without a shipping_provider attached (e.g. legacy data,
    home delivery without a courier) must silently no-op."""
    order = OrderFactory()  # no shipping_provider

    # Should not raise; nothing to assert beyond "no exception".
    ShippingService.dispatch_create_shipment_task(order)
