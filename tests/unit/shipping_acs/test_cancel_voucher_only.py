"""Cancelling only the ACS voucher, leaving the order alone.

The one cancel path was the order's: ``cancel_attached_shipment`` ran only
when the whole order was cancelled. A corrected address needs the voucher
deleted at ACS and a new one issued, with the order still open, so the
voucher admin has its own action. ACS refuses a voucher already on an
issued pickup list (``ACS_Delete_Voucher``).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages import constants as messages
from django.test import RequestFactory

from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from shipping_acs.admin import AcsShipmentAdmin
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.exceptions import AcsAPIError
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsShipment
from shipping_acs.services import AcsService

pytestmark = pytest.mark.django_db


def _call(shipment, reason=""):
    """Press the dialog's button: unfold validates the posted form first."""
    request = RequestFactory().post(
        "/", {"reason": reason, "_form_submitted": "on"}
    )
    request.user = get_user_model()(
        id=1, username="staff", is_staff=True, is_superuser=True
    )
    admin = AcsShipmentAdmin(AcsShipment, AdminSite())
    with patch.object(admin, "message_user") as say:
        response = admin.cancel_voucher_only(request, object_id=shipment.pk)
    return response, say


def test_the_action_is_offered_on_the_detail_page():
    assert "cancel_voucher_only" in AcsShipmentAdmin.actions_detail


def test_it_cancels_the_voucher_and_not_the_order():
    shipment = AcsShipmentFactory(
        order=OrderFactory(status=OrderStatus.PROCESSING),
        voucher_no="9700000101",
        shipment_state=AcsShipmentState.NEW,
    )

    with patch.object(AcsService, "cancel_voucher") as cancel:
        response, say = _call(shipment, reason="new address")

    cancel.assert_called_once()
    assert cancel.call_args.kwargs["reason"] == "new address"
    assert response.status_code == 200
    assert "HX-Redirect" in response.headers
    assert say.call_args.args[2] == messages.SUCCESS
    shipment.order.refresh_from_db()
    assert shipment.order.status == OrderStatus.PROCESSING


def test_a_voucher_on_an_issued_pickup_list_is_refused_with_the_reason():
    shipment = AcsShipmentFactory(voucher_no="9700000102")
    refusal = AcsAPIError(
        alias="ACS_Delete_Voucher",
        error_message="Voucher is in a pickup list",
    )

    with patch.object(AcsService, "cancel_voucher", side_effect=refusal):
        _response, say = _call(shipment)

    assert say.call_args.args[2] == messages.ERROR
    assert "pickup list" in str(say.call_args.args[1])


def test_an_already_cancelled_voucher_is_not_sent_to_acs_again():
    shipment = AcsShipmentFactory(
        voucher_no="9700000103", shipment_state=AcsShipmentState.CANCELED
    )

    with patch.object(AcsService, "cancel_voucher") as cancel:
        _response, say = _call(shipment)

    cancel.assert_not_called()
    assert say.call_args.args[2] == messages.WARNING
