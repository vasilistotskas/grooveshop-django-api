"""The shipment admin's cancel actions ask the service which parcels cancel.

``BoxNowService.can_cancel_shipment`` is the one rule: a shipment BoxNow
never received (no ``parcel_id``) cancels locally, a parcel cancels while
it is NEW. The admin used to test ``parcel_state == NEW`` itself, so it
refused a ``pending_creation`` shipment the service cancels, and a change
to the service's rule would not have reached these two actions.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from shipping_boxnow.admin import BoxNowShipmentAdmin
from shipping_boxnow.enum import BoxNowParcelState
from shipping_boxnow.factories import BoxNowShipmentFactory
from shipping_boxnow.models import BoxNowShipment
from shipping_boxnow.services import BoxNowService

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

CLIENT = "shipping_boxnow.services.BoxNowClient"


def _request():
    request = RequestFactory().post("/")
    request.user = get_user_model()(id=1, username="staff")
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _admin():
    return BoxNowShipmentAdmin(BoxNowShipment, AdminSite())


def _press_detail(shipment):
    request = _request()
    _admin().cancel_parcel_action(request, shipment.pk)
    return [str(m) for m in request._messages]


def _press_bulk(*shipments):
    request = _request()
    _admin().cancel_parcels(
        request,
        BoxNowShipment.objects.filter(pk__in=[s.pk for s in shipments]),
    )
    return [str(m) for m in request._messages]


def _never_sent():
    return BoxNowShipmentFactory(
        parcel_id=None,
        delivery_request_id=None,
        parcel_state=BoxNowParcelState.PENDING_CREATION,
    )


def _parcel(state):
    return BoxNowShipmentFactory(with_parcel=True, parcel_state=state)


class TestDetailAction:
    def test_a_shipment_boxnow_never_received_is_cancelled_locally(self):
        shipment = _never_sent()

        with patch(CLIENT, MagicMock()) as client:
            messages = _press_detail(shipment)

        client.return_value.cancel_parcel.assert_not_called()
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED
        assert any("canceled successfully" in m for m in messages)

    def test_a_new_parcel_is_cancelled_at_boxnow(self):
        shipment = _parcel(BoxNowParcelState.NEW)

        with patch(CLIENT, MagicMock()) as client:
            _press_detail(shipment)

        client.return_value.cancel_parcel.assert_called_once_with(
            shipment.parcel_id
        )
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED

    def test_a_parcel_past_new_is_refused_without_calling_boxnow(self):
        shipment = _parcel(BoxNowParcelState.IN_DEPOT)

        with patch(CLIENT, MagicMock()) as client:
            messages = _press_detail(shipment)

        client.return_value.cancel_parcel.assert_not_called()
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.IN_DEPOT
        assert any("cannot be canceled" in m for m in messages)

    @pytest.mark.parametrize("allowed", [True, False])
    def test_the_service_decides_not_the_admin(self, allowed):
        """Whatever the service's rule answers is what the action does."""
        shipment = _parcel(
            BoxNowParcelState.IN_DEPOT if allowed else BoxNowParcelState.NEW
        )

        with (
            patch.object(
                BoxNowService, "can_cancel_shipment", return_value=allowed
            ),
            patch.object(BoxNowService, "cancel_shipment") as cancel,
        ):
            _press_detail(shipment)

        assert cancel.called is allowed


class TestBulkAction:
    def test_it_cancels_what_the_service_allows_and_skips_the_rest(self):
        never_sent = _never_sent()
        new = _parcel(BoxNowParcelState.NEW)
        in_depot = _parcel(BoxNowParcelState.IN_DEPOT)

        with patch(CLIENT, MagicMock()) as client:
            messages = _press_bulk(never_sent, new, in_depot)

        client.return_value.cancel_parcel.assert_called_once_with(new.parcel_id)
        for shipment in (never_sent, new):
            shipment.refresh_from_db()
            assert shipment.parcel_state == BoxNowParcelState.CANCELED
        in_depot.refresh_from_db()
        assert in_depot.parcel_state == BoxNowParcelState.IN_DEPOT
        assert any("2 parcel(s) canceled" in m for m in messages)
        assert any("1 shipment(s) skipped" in m for m in messages)

    def test_the_service_decides_not_the_admin(self):
        new = _parcel(BoxNowParcelState.NEW)

        with (
            patch.object(
                BoxNowService, "can_cancel_shipment", return_value=False
            ),
            patch.object(BoxNowService, "cancel_shipment") as cancel,
        ):
            messages = _press_bulk(new)

        cancel.assert_not_called()
        assert any("1 shipment(s) skipped" in m for m in messages)
