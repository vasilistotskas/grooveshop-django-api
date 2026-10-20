"""``Create BoxNow parcel now`` also replaces a cancelled parcel.

A cancelled parcel keeps its ``delivery_request_id``, and the creation task
returns an existing shipment unchanged, so a cancelled-before-pickup order
could never be shipped again. ACS has a reissue path; the button now resets
the cancelled parcel first, as long as the order itself is still wanted.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from shipping_boxnow.admin import BoxNowShipmentAdmin
from shipping_boxnow.enum import BoxNowParcelState
from shipping_boxnow.factories import BoxNowShipmentFactory
from shipping_boxnow.models import BoxNowShipment

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

TASK = "shipping_boxnow.tasks.create_boxnow_shipment_for_order.delay"


def _press(shipment):
    request = RequestFactory().post("/")
    request.user = get_user_model()(id=1, username="staff")
    request.session = {}
    request._messages = FallbackStorage(request)
    admin = BoxNowShipmentAdmin(BoxNowShipment, AdminSite())
    response = admin.create_parcel_action(request, shipment.pk)
    return response, [str(m) for m in request._messages]


def _cancelled(order_status=OrderStatus.PROCESSING):
    order = OrderFactory(
        status=order_status, payment_status=PaymentStatus.COMPLETED
    )
    return BoxNowShipmentFactory(
        order=order,
        with_parcel=True,
        parcel_state=BoxNowParcelState.CANCELED,
    )


def test_a_cancelled_parcel_is_reset_and_a_new_one_requested():
    shipment = _cancelled()
    old_request = shipment.delivery_request_id

    with patch(TASK) as task:
        _press(shipment)

    task.assert_called_once_with(shipment.order_id)
    shipment.refresh_from_db()
    assert shipment.delivery_request_id is None
    assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION
    assert shipment.metadata["previous_parcels"][0]["delivery_request_id"] == (
        old_request
    )


def test_a_cancelled_order_is_not_shipped_again():
    shipment = _cancelled(order_status=OrderStatus.CANCELED)

    with patch(TASK) as task:
        _response, messages = _press(shipment)

    task.assert_not_called()
    shipment.refresh_from_db()
    assert shipment.parcel_state == BoxNowParcelState.CANCELED
    assert any("canceled" in m for m in messages)


def test_a_live_parcel_is_left_alone():
    shipment = BoxNowShipmentFactory(
        with_parcel=True, parcel_state=BoxNowParcelState.NEW
    )
    parcel = shipment.parcel_id

    with patch(TASK) as task:
        _press(shipment)

    task.assert_not_called()
    shipment.refresh_from_db()
    assert shipment.parcel_id == parcel
