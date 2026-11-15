"""A carrier that gives no answer is a 503, never a refusal (#650).

``CarrierUnavailableError`` (a 5xx, a timeout, a connection or auth
failure, no credentials) is what both carriers raise when they did not
judge the request. The views that call a carrier answer it with 503
``carrier_unavailable`` and a ``detail`` written for a client; the
carrier's error text, which names hosts and ``requests`` internals, goes
to the log only. A refusal by the carrier keeps its own answer.

Before, the BOX NOW nearest-locker lookup answered an outage with the
same 400 as "no locker near this address", so the agent gateway told a
shopper there were no lockers nearby while BOX NOW was down.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from shipping_acs.exceptions import (
    AcsAPIError,
    AcsAuthError,
    AcsOutcomeUnknownError,
    AcsRetryableError,
)
from shipping_acs.factories import AcsShipmentFactory
from shipping_boxnow.exceptions import (
    BoxNowAPIError,
    BoxNowAuthError,
    BoxNowConfigError,
    BoxNowRetryableError,
)
from shipping_boxnow.factories import BoxNowShipmentFactory
from tests.utils.staff import stamp_platform_identity
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

INTERNAL = "HTTPSConnectionPool(host='api.carrier.test', port=443)"


def _assert_unavailable(response) -> None:
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    body = response.json()
    assert body["code"] == "carrier_unavailable"
    assert body["detail"]
    assert "HTTPSConnectionPool" not in body["detail"]


BOXNOW_OUTAGES = [
    pytest.param(
        BoxNowRetryableError(0, message=f"BoxNow connection error: {INTERNAL}"),
        id="connection",
    ),
    pytest.param(
        BoxNowRetryableError(
            0, message=f"BoxNow timeout after 10s: {INTERNAL}"
        ),
        id="timeout",
    ),
    pytest.param(
        BoxNowRetryableError(503, message="BoxNow server error 503"), id="5xx"
    ),
    pytest.param(BoxNowAuthError(401, message="Unauthorized"), id="auth"),
    pytest.param(
        BoxNowConfigError("Missing required Tenant BoxNow fields"),
        id="not-configured",
    ),
]

ACS_OUTAGES = [
    pytest.param(
        AcsRetryableError(
            alias="X", error_message=f"ACS connection error: {INTERNAL}"
        ),
        id="never-connected",
    ),
    pytest.param(
        AcsOutcomeUnknownError(
            alias="X", error_message=f"ACS timeout after 15s: {INTERNAL}"
        ),
        id="lost-answer",
    ),
    pytest.param(
        AcsAuthError(alias="X", error_message="auth layer", http_status=406),
        id="auth",
    ),
]


class TestBoxNowNearestLocker:
    @staticmethod
    def _post(error: Exception):
        client = MagicMock()
        client.return_value.find_closest_locker.side_effect = error
        with patch("shipping_boxnow.client.BoxNowClient", client):
            return APIClient().post(
                reverse("shipping-boxnow-locker-nearest"),
                {
                    "city": "Chalandri",
                    "street": "Leoforos 125",
                    "postalCode": "15232",
                },
                format="json",
            )

    @pytest.mark.parametrize("error", BOXNOW_OUTAGES)
    def test_an_outage_is_a_503(self, error):
        _assert_unavailable(self._post(error))

    def test_no_locker_near_the_address_stays_a_400_with_boxnows_code(self):
        response = self._post(
            BoxNowAPIError(400, code="P423", message="No nearby locker.")
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "P423"


class TestAcsAddressValidation:
    @staticmethod
    def _post(error: Exception):
        with patch("shipping_acs.client.AcsClient") as client:
            client.return_value.address_validation.side_effect = error
            return APIClient().post(
                reverse("shipping-acs-address-validation"),
                {"address": "ΠΕΙΡΑΙΩΣ 25 17778"},
                format="json",
            )

    @pytest.mark.parametrize("error", ACS_OUTAGES)
    def test_an_outage_is_a_503(self, error):
        _assert_unavailable(self._post(error))

    def test_an_acs_refusal_keeps_its_answer(self):
        response = self._post(
            AcsAPIError(alias="X", error_message="bad address")
        )

        assert response.status_code == status.HTTP_502_BAD_GATEWAY
        assert response.json()["code"] == "acs_address_validation_failed"


@pytest.fixture
def staff_client():
    client = APIClient()
    client.force_authenticate(
        user=stamp_platform_identity(
            UserAccountFactory(
                is_superuser=True, is_staff=True, num_addresses=0
            )
        )
    )
    return client


class TestStaffCancels:
    @pytest.mark.parametrize("error", BOXNOW_OUTAGES)
    def test_a_boxnow_outage_is_a_503(self, staff_client, error):
        shipment = BoxNowShipmentFactory(parcel_id="1234567890")
        with patch(
            "shipping_boxnow.services.BoxNowService.cancel_shipment",
            side_effect=error,
        ):
            response = staff_client.post(
                reverse(
                    "shipping-boxnow-cancel",
                    kwargs={"parcel_id": shipment.parcel_id},
                )
            )

        _assert_unavailable(response)

    @pytest.mark.parametrize("error", ACS_OUTAGES)
    def test_an_acs_outage_is_a_503(self, staff_client, error):
        shipment = AcsShipmentFactory(voucher_no="9001999001")
        with patch(
            "shipping_acs.services.AcsService.cancel_voucher",
            side_effect=error,
        ):
            response = staff_client.post(
                reverse(
                    "shipping-acs-cancel",
                    kwargs={"voucher_no": shipment.voucher_no},
                )
            )

        _assert_unavailable(response)

    def test_a_refusal_stays_a_400(self, staff_client):
        shipment = BoxNowShipmentFactory(parcel_id="1234567890")
        with patch(
            "shipping_boxnow.services.BoxNowService.cancel_shipment",
            side_effect=BoxNowAPIError(409, code="P420", message="Not new."),
        ):
            response = staff_client.post(
                reverse(
                    "shipping-boxnow-cancel",
                    kwargs={"parcel_id": shipment.parcel_id},
                )
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "P420"
