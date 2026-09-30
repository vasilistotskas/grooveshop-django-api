"""Unit tests for the Phase 3 generic dispatch helpers.

Validates that ``ShippingService.create_shipment_row_for_order`` and
``ShippingService.dispatch_create_shipment_task`` route to the
right adapter via the registry — and gracefully no-op when the order
has no provider attached.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import transaction

from order.enum.status import OrderStatus, PaymentStatus
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


def _defer_on_commit(monkeypatch):
    """Restore Django's deferral, which the suite's conftest replaces
    with an immediate call: these tests are about what the enclosing
    transaction does to the callback."""
    monkeypatch.setattr(
        transaction,
        "on_commit",
        lambda func, using=None, robust=False: transaction.get_connection(
            using
        ).on_commit(func, robust),
    )


def test_create_shipment_row_dispatches_to_boxnow_carrier():
    """When the order has provider=boxnow, the BoxNow carrier's
    create_shipment_row hook is invoked with the right kind + payload."""
    order = OrderFactory(
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        num_order_items=0,
    )
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
    order = OrderFactory(
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        num_order_items=0,
    )  # no shipping_provider set

    with (
        patch(
            "shipping_boxnow.carrier.BoxNowCarrier.create_shipment_row"
        ) as boxnow_hook,
        patch(
            "shipping_acs.carrier.AcsCarrier.create_shipment_row"
        ) as acs_hook,
    ):
        ShippingService.create_shipment_row_for_order(order)

    boxnow_hook.assert_not_called()
    acs_hook.assert_not_called()


def test_dispatch_task_dispatches_to_boxnow_via_provider_fk():
    """Orders with shipping_provider=boxnow attached dispatch through
    the BoxNow carrier's Celery task hook."""
    order = OrderFactory(
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        num_order_items=0,
    )
    _attach_provider(order, "boxnow")

    with patch(
        "shipping_boxnow.carrier.BoxNowCarrier.dispatch_create_shipment_task"
    ) as mock_dispatch:
        ShippingService.dispatch_create_shipment_task(order)

    assert mock_dispatch.called


def test_dispatch_task_dispatches_to_acs_via_provider_fk():
    order = OrderFactory(
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        num_order_items=0,
    )
    _attach_provider(order, "acs")

    with patch(
        "shipping_acs.carrier.AcsCarrier.dispatch_create_shipment_task"
    ) as mock_dispatch:
        ShippingService.dispatch_create_shipment_task(order)

    assert mock_dispatch.called


def test_dispatch_task_no_op_for_orders_without_provider():
    """Orders without a shipping_provider attached (e.g. legacy data,
    home delivery without a courier) must silently no-op."""
    order = OrderFactory(
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        num_order_items=0,
    )  # no shipping_provider

    with (
        patch(
            "shipping_boxnow.carrier.BoxNowCarrier.dispatch_create_shipment_task"
        ) as boxnow_dispatch,
        patch(
            "shipping_acs.carrier.AcsCarrier.dispatch_create_shipment_task"
        ) as acs_dispatch,
    ):
        ShippingService.dispatch_create_shipment_task(order)

    boxnow_dispatch.assert_not_called()
    acs_dispatch.assert_not_called()


def test_dispatch_task_does_not_fire_when_outer_txn_rolls_back(
    monkeypatch, django_capture_on_commit_callbacks
):
    """Regression for commit 59527a87: dispatch must be wrapped in
    ``transaction.on_commit`` so the courier task never enqueues for
    an order whose creating transaction never committed.

    Without that wrap the worker observed "Order N not found" and
    abandoned the voucher mint permanently (verified on prod order
    47, 2026-04-30). This test rolls the outer transaction back and
    asserts the carrier's per-provider dispatcher was never reached.
    """
    order = OrderFactory(
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        num_order_items=0,
    )
    _attach_provider(order, "boxnow")
    _defer_on_commit(monkeypatch)

    with (
        patch(
            "shipping_boxnow.carrier.BoxNowCarrier.dispatch_create_shipment_task"
        ) as mock_dispatch,
        django_capture_on_commit_callbacks(execute=True),
        transaction.atomic(),
    ):
        ShippingService.dispatch_create_shipment_task(order)
        transaction.set_rollback(True)

    assert mock_dispatch.call_count == 0, (
        "BoxNowCarrier.dispatch_create_shipment_task fired despite the "
        "outer transaction rolling back — on_commit guard is missing."
    )


def test_dispatch_task_fires_when_outer_txn_commits(
    monkeypatch, django_capture_on_commit_callbacks
):
    """Positive case: when the outer atomic block commits cleanly,
    the on_commit callback runs and the carrier's dispatcher is
    invoked exactly once."""
    order = OrderFactory(
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        num_order_items=0,
    )
    _attach_provider(order, "boxnow")
    _defer_on_commit(monkeypatch)

    with (
        patch(
            "shipping_boxnow.carrier.BoxNowCarrier.dispatch_create_shipment_task"
        ) as mock_dispatch,
        django_capture_on_commit_callbacks(execute=True),
        transaction.atomic(),
    ):
        ShippingService.dispatch_create_shipment_task(order)
        # The block exits cleanly, so the callback stays queued and the
        # capture runs it as the test transaction's commit would.

    assert mock_dispatch.call_count == 1
