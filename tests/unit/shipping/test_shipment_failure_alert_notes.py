"""A courier rejection is recorded on the order, not only mailed.

Prod order 316 (2026-09-24): ACS rejected the voucher, the ops email
went out, and the order page — where staff look when the customer asks
where the parcel is — showed nothing. The alert now also writes an
``OrderHistory`` note carrying the carrier's own next step.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.models.history import OrderHistory
from pay_way.factories import PayWayFactory
from shipping.alerts import alert_admins_shipment_creation_failed
from shipping_acs.factories import AcsShipmentFactory
from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.exceptions import BoxNowAPIError
from shipping_boxnow.factories import BoxNowShipmentFactory

pytestmark = pytest.mark.django_db


def _notes(order_id):
    return [
        h.new_value["note"]
        for h in OrderHistory.objects.filter(
            order_id=order_id, change_type="NOTE"
        )
    ]


def _cod_order():
    return OrderFactory(
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        pay_way=PayWayFactory(),
    )


def test_the_alert_notes_the_error_and_the_remedy_on_the_order():
    order = _cod_order()

    with patch("shipping.alerts.send_ops_alert", return_value=True) as mail:
        alert_admins_shipment_creation_failed(
            order_id=order.id,
            carrier="ACS",
            error="ACS [ACS_Create_Voucher]: fill data error",
            remedy="Fix the address on the order, then press X.",
        )

    [note] = [n for n in _notes(order.id) if "shipment creation failed" in n]
    assert "fill data error" in note
    assert "Fix the address on the order, then press X." in note
    assert "Fix the address on the order" in mail.call_args.kwargs["message"]


def test_a_note_failure_never_blocks_the_email():
    order = _cod_order()

    with (
        patch(
            "order.models.history.OrderHistory.log_note",
            side_effect=RuntimeError("db down"),
        ),
        patch("shipping.alerts.send_ops_alert", return_value=True) as mail,
    ):
        alert_admins_shipment_creation_failed(
            order_id=order.id, carrier="ACS", error="e", remedy="r"
        )

    mail.assert_called_once()


def test_acs_business_error_tells_staff_to_fix_the_address(
    acs_configured_tenant,
):
    from shipping_acs.tasks import create_acs_voucher_for_order

    order = _cod_order()
    AcsShipmentFactory(order=order)
    client = MagicMock()
    client.return_value.billing_code = "B"
    client.return_value.create_voucher.return_value = {
        "Voucher_No": "",
        "Error_Message": "ACS:Error fill data error",
    }

    with (
        patch("shipping_acs.services.AcsClient", client),
        patch("shipping.alerts.send_ops_alert", return_value=True),
    ):
        result = create_acs_voucher_for_order.run(order.id)

    assert result["status"] == "acs_api_error"
    [note] = [n for n in _notes(order.id) if "shipment creation failed" in n]
    assert "Fix the address on the order" in note
    assert "Issue ACS voucher now" in note


def test_boxnow_business_error_points_at_the_parcel_button(
    boxnow_configured_tenant,
):
    from shipping_boxnow.tasks import create_boxnow_shipment_for_order

    order = _cod_order()
    BoxNowShipmentFactory(
        order=order,
        locker_external_id="4",
        parcel_state=BoxNowParcelState.PENDING_CREATION,
    )
    client = MagicMock()
    client.return_value.create_delivery_request.side_effect = BoxNowAPIError(
        400, code="P402", message="invalid locker"
    )

    with (
        patch("shipping_boxnow.services.BoxNowClient", client),
        patch("shipping.alerts.send_ops_alert", return_value=True),
    ):
        result = create_boxnow_shipment_for_order.run(order.id)

    assert result["status"] == "boxnow_api_error"
    [note] = [n for n in _notes(order.id) if "shipment creation failed" in n]
    assert "Create BoxNow parcel now" in note
