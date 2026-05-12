"""Integration tests for the BoxNow webhook endpoint.

Tests the full request path through Django + DRF: URL routing,
signature verification, and service dispatch.

Multi-tenant note: production resolves the owning tenant by iterating
``Tenant.objects.filter(...).exclude(schema_name=public)`` and entering
each schema via ``schema_context`` to look up the parcel. Tests can't
spin up real Postgres schemas cheaply, so the autouse fixture below
creates a ``Tenant`` row (``auto_create_schema=False``) and patches
``schema_context`` in the webhook view to a no-op — same pattern as
``tests/integration/tenant/test_multi_tenant_invariants.py``. The
shipment then lives in the test's public schema and the resolver
"finds" it under the patched no-op context.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from shipping_boxnow.factories import BoxNowShipmentFactory
from tenant.models import Tenant


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


@contextmanager
def _noop_schema(_schema):
    """Drop-in replacement for ``schema_context`` that does nothing.

    Used by ``_tenant_setup`` so the webhook resolver's iteration over
    non-public tenants doesn't try to SET search_path to a schema that
    doesn't exist in the test Postgres database.
    """
    yield


@pytest.fixture
def _tenant_setup(db):
    """Provision a single non-public tenant + no-op ``schema_context``.

    The webhook resolver requires at least one ``Tenant`` row with a
    non-public ``schema_name`` to iterate. Production hits the real
    tenant's schema via ``schema_context`` and looks up the parcel
    there; tests keep the parcel in the public schema and patch
    ``schema_context`` to a no-op so the same lookup runs against
    public. The downstream Celery task (``process_boxnow_webhook_event``)
    inherits ``TenantTask`` and would also enter the schema — we patch
    its ``schema_context`` too so the eager-mode execution doesn't trip
    on the missing schema.
    """
    # ``auto_create_schema`` is a django-tenants class attribute, not a
    # model field — set after __init__, before save(), so the row lands
    # without trying to ``CREATE SCHEMA`` against the test database.
    t = Tenant(
        schema_name="boxnow_webhook_test",
        name="boxnow-webhook-test",
        slug="boxnow-webhook-test",
        owner_email="owner-boxnow-webhook-test@example.com",
        is_active=True,
        suspended_at=None,
    )
    t.auto_create_schema = False
    t.save()
    # The webhook view imports ``schema_context`` at module scope, so
    # patch its bound reference. ``TenantTask.__call__`` (the Celery
    # base class used by ``process_boxnow_webhook_event``) imports
    # ``schema_context`` lazily inside the call, so patch the canonical
    # ``django_tenants.utils`` reference too — that catches the lazy
    # import on first execution under ``CELERY_TASK_ALWAYS_EAGER``.
    with (
        patch(
            "shipping_boxnow.views.webhook.schema_context",
            _noop_schema,
        ),
        patch(
            "django_tenants.utils.schema_context",
            _noop_schema,
        ),
    ):
        yield


@pytest.mark.django_db
class TestWebhookEndpoint:
    @pytest.fixture(autouse=True)
    def _setup(self, _tenant_setup):
        """Autouse fixture so every test in this class gets the
        tenant + no-op ``schema_context`` patch without an explicit
        argument."""

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
        """When BOXNOW_WEBHOOK_SECRET is not configured → 503.

        Post-multi-tenant: the secret check runs INSIDE the resolved
        tenant's schema_context, so the parcel must first be findable
        in the DB before the secret-missing branch can fire. Without
        a matching shipment, the resolver returns ``None`` and the
        webhook short-circuits with 200 (orphan parcel) before ever
        reaching the secret lookup. Seed a shipment so the test
        actually exercises the 503 path it claims to.
        """
        settings.BOXNOW_WEBHOOK_SECRET = ""

        shipment = BoxNowShipmentFactory(with_parcel=True)
        raw = _build_raw_body(parcel_id=shipment.parcel_id, event="new")

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
