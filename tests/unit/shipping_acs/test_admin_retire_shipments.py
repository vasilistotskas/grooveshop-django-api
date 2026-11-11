"""The "Retire selected shipments" changelist action.

Local bookkeeping for a voucher ACS will never advance again: the row
becomes CANCELED so the poller stops, ACS is not called and the order is
not touched. A shipment already in one of ``TERMINAL_STATES`` is left as
it is.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from shipping_acs.admin import AcsShipmentAdmin
from shipping_acs.enum.shipment_state import TERMINAL_STATES, AcsShipmentState
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsShipment

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _retire(*shipments):
    admin = AcsShipmentAdmin(AcsShipment, AdminSite())
    request = RequestFactory().post("/")
    request.user = get_user_model()(id=7, username="staff", is_superuser=True)
    with (
        patch.object(admin, "message_user") as message,
        patch("shipping_acs.services.AcsClient") as client,
    ):
        admin.retire_shipments(
            request,
            AcsShipment.objects.filter(pk__in=[s.pk for s in shipments]),
        )
    assert not client.called, "retiring is local: ACS must not be called"
    return str(message.call_args.args[1])


def test_a_live_shipment_is_retired_and_its_order_left_alone():
    shipment = AcsShipmentFactory(
        voucher_no="9800000001", shipment_state=AcsShipmentState.IN_TRANSIT
    )
    order_status = shipment.order.status

    reported = _retire(shipment)

    shipment.refresh_from_db()
    assert shipment.shipment_state == AcsShipmentState.CANCELED
    assert shipment.is_active is False
    assert shipment.order.status == order_status
    assert shipment.history.latest().history_change_reason == (
        "Retired via admin action"
    )
    assert "1 shipment(s)" in reported


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
def test_a_terminal_shipment_is_skipped(state):
    shipment = AcsShipmentFactory(voucher_no="9800000002", shipment_state=state)
    history_rows = shipment.history.count()

    reported = _retire(shipment)

    shipment.refresh_from_db()
    assert shipment.shipment_state == state
    assert shipment.history.count() == history_rows
    assert "0 shipment(s)" in reported


def test_the_skipped_states_are_the_terminal_states():
    """The action reads ``TERMINAL_STATES``; it keeps no list of its own."""
    shipment = AcsShipmentFactory(
        voucher_no="9800000003", shipment_state=AcsShipmentState.IN_TRANSIT
    )

    with patch(
        "shipping_acs.admin.TERMINAL_STATES",
        frozenset({AcsShipmentState.IN_TRANSIT}),
    ):
        _retire(shipment)

    shipment.refresh_from_db()
    assert shipment.shipment_state == AcsShipmentState.IN_TRANSIT
