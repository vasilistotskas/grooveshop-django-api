"""Integration tests for the BoxNow webhook endpoint.

Tests the full request path through Django + DRF: URL routing,
signature verification, and service dispatch.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from shipping_boxnow.factories import BoxNowShipmentFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WEBHOOK_SECRET = "test-webhook-secret-abc123"


def _sign(data_bytes: bytes, secret: str = _WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), data_bytes, hashlib.sha256).hexdigest()


def _build_raw_body(
    parcel_id: str,
    event: str,
    message_id: str = "msg-test-001",
    event_time: str = "2025-01-15T10:30:00Z",
) -> bytes:
    """Build a realistic raw webhook body that satisfies the signature."""
    data_obj = {
        "parcelId": parcel_id,
        "parcelState": event,
        "event": event,
        "time": event_time,
        "orderNumber": "ORD-TEST",
        "eventLocation": {"displayName": "Test Locker", "postalCode": "11521"},
        "customer": {
            "name": "Test Customer",
            "email": "c@test.com",
            "phone": "+302100000000",
        },
    }
    data_str = json.dumps(data_obj, separators=(",", ":"))
    datasignature = _sign(data_str.encode())

    # Build raw body where the "data" key appears exactly as in data_str so
    # extract_data_substring can find it.  We embed the compact data_str
    # inside the outer JSON manually to guarantee the substring is present.
    outer = (
        '{"specversion":"1.0",'
        '"type":"gr.boxnow.parcel_event_change",'
        '"source":"boxnow-stage",'
        f'"subject":"{parcel_id}",'
        f'"id":"{message_id}",'
        f'"time":"{event_time}",'
        '"datacontenttype":"application/json",'
        f'"datasignature":"{datasignature}",'
        f'"data":{data_str}'
        "}"
    )
    return outer.encode()


def _webhook_url():
    return reverse("boxnow-webhook")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWebhookEndpoint:
    def setup_method(self):
        self.client = APIClient()

    def test_invalid_signature_returns_401(self, settings):
        """Tampered body or wrong signature → 401."""
        settings.BOXNOW_WEBHOOK_SECRET = _WEBHOOK_SECRET

        shipment = BoxNowShipmentFactory(with_parcel=True)
        raw = _build_raw_body(parcel_id=shipment.parcel_id, event="in-depot")

        # Tamper the signature by sending a wrong one.
        tampered_body = raw.replace(
            b'"datasignature":"',
            b'"datasignature":"wrong',
            1,
        )

        response = self.client.post(
            _webhook_url(),
            data=tampered_body,
            content_type="application/json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_valid_signature_returns_200_and_creates_event(self, settings):
        """Valid signature with known shipment → 200 + BoxNowParcelEvent."""
        settings.BOXNOW_WEBHOOK_SECRET = _WEBHOOK_SECRET

        from shipping_boxnow.models import BoxNowParcelEvent

        shipment = BoxNowShipmentFactory(with_parcel=True)
        message_id = f"valid-msg-{shipment.parcel_id}"
        raw = _build_raw_body(
            parcel_id=shipment.parcel_id,
            event="in-depot",
            message_id=message_id,
        )

        # Patch arrival notification to avoid email template errors.
        with patch(
            "shipping_boxnow.tasks.boxnow_send_arrival_notification.delay"
        ):
            response = self.client.post(
                _webhook_url(),
                data=raw,
                content_type="application/json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert BoxNowParcelEvent.objects.filter(
            webhook_message_id=message_id
        ).exists()

    def test_duplicate_message_id_returns_200_no_duplicate_event(
        self, settings
    ):
        """Sending the same message_id twice → 200 both times, one event row."""
        settings.BOXNOW_WEBHOOK_SECRET = _WEBHOOK_SECRET

        from shipping_boxnow.models import BoxNowParcelEvent

        shipment = BoxNowShipmentFactory(with_parcel=True)
        message_id = f"dup-msg-{shipment.parcel_id}"
        raw = _build_raw_body(
            parcel_id=shipment.parcel_id,
            event="in-depot",
            message_id=message_id,
        )

        with patch(
            "shipping_boxnow.tasks.boxnow_send_arrival_notification.delay"
        ):
            r1 = self.client.post(
                _webhook_url(),
                data=raw,
                content_type="application/json",
            )
            r2 = self.client.post(
                _webhook_url(),
                data=raw,
                content_type="application/json",
            )

        assert r1.status_code == status.HTTP_200_OK
        assert r2.status_code == status.HTTP_200_OK
        # Exactly one event row despite two requests.
        assert (
            BoxNowParcelEvent.objects.filter(
                webhook_message_id=message_id
            ).count()
            == 1
        )

    def test_missing_secret_returns_503(self, settings):
        """When BOXNOW_WEBHOOK_SECRET is not configured → 503."""
        settings.BOXNOW_WEBHOOK_SECRET = ""

        raw = _build_raw_body(parcel_id="9219709201", event="new")

        response = self.client.post(
            _webhook_url(),
            data=raw,
            content_type="application/json",
        )
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_invalid_envelope_returns_400(self, settings):
        """Bad JSON or wrong specversion → 400."""
        settings.BOXNOW_WEBHOOK_SECRET = _WEBHOOK_SECRET

        bad_body = b'{"specversion":"99","type":"wrong","id":"x","data":{}}'
        response = self.client.post(
            _webhook_url(),
            data=bad_body,
            content_type="application/json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_malformed_json_returns_400(self, settings):
        """Non-JSON body returns 400."""
        settings.BOXNOW_WEBHOOK_SECRET = _WEBHOOK_SECRET

        response = self.client.post(
            _webhook_url(),
            data=b"not-json-at-all",
            content_type="application/json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
