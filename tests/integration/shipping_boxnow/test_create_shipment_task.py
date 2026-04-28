"""Integration tests for the create_boxnow_shipment_for_order Celery task.

Celery runs in eager mode (CELERY_TASK_ALWAYS_EAGER=True) per conftest.py,
so tasks execute synchronously in tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from shipping_boxnow.exceptions import BoxNowAPIError
from shipping_boxnow.factories import BoxNowShipmentFactory
from shipping_boxnow.tasks import create_boxnow_shipment_for_order


@pytest.mark.django_db
class TestCreateBoxNowShipmentTask:
    def test_task_calls_service_with_order(self):
        """Task calls BoxNowService.create_shipment_for_order with the order."""
        order = OrderFactory(
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
        )
        BoxNowShipmentFactory(order=order)

        mock_service = MagicMock()
        mock_service.return_value = MagicMock(
            parcel_id="9219709201", delivery_request_id="42224"
        )

        with patch(
            "shipping_boxnow.services.BoxNowService.create_shipment_for_order",
            mock_service,
        ):
            result = create_boxnow_shipment_for_order(order.id)

        mock_service.assert_called_once()
        call_arg = mock_service.call_args[0][0]
        assert call_arg.id == order.id
        assert result["status"] == "ok"
        assert result["parcel_id"] == "9219709201"

    def test_task_handles_missing_order(self):
        """Task returns order_not_found status without raising for unknown id."""
        result = create_boxnow_shipment_for_order(999999999)

        assert result["status"] == "order_not_found"
        assert result["order_id"] == 999999999

    def test_task_handles_boxnow_api_error(self):
        """BoxNowAPIError from service returns boxnow_api_error without retry."""
        order = OrderFactory(
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
        )
        BoxNowShipmentFactory(order=order)

        api_err = BoxNowAPIError(
            400, code="P402", message="Invalid destination"
        )

        with patch(
            "shipping_boxnow.services.BoxNowService.create_shipment_for_order",
            side_effect=api_err,
        ):
            result = create_boxnow_shipment_for_order(order.id)

        assert result["status"] == "boxnow_api_error"
        assert result["code"] == "P402"
        assert result["order_id"] == order.id
