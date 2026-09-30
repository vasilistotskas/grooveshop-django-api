"""Tenant resolution, signed-data parsing and dispatch in the BoxNow webhook.

Covers ``shipping_boxnow/views/webhook.py``: ``_resolve_tenant_for_parcel``
skipping inactive and suspended tenants, the 400 for signed ``data`` that
does not parse, the 500 when the Celery broker refuses the task, and the
schema header + content fingerprint on a successful dispatch.

Tenant schemas are not physically created (``auto_create_schema=False``),
so ``schema_context`` / ``tenant_context`` are replaced by stand-ins — the
same approach as ``test_webhook_endpoint.py``. The broker (``apply_async``)
is the only external boundary mocked.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.urls import reverse
from django.utils import timezone
from kombu.exceptions import OperationalError
from rest_framework.test import APIClient

from shipping_boxnow.factories import BoxNowShipmentFactory
from tenant.models import Tenant

pytestmark = pytest.mark.django_db

_SECRET = "branch-test-webhook-secret"


def _signed_body(parcel_id: str) -> tuple[bytes, bytes]:
    data = json.dumps(
        {"parcelId": parcel_id, "event": "in-depot", "parcelState": "in-depot"},
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(_SECRET.encode(), data, hashlib.sha256).hexdigest()
    body = (
        b'{"specversion":"1.0","type":"gr.boxnow.parcel_event_change",'
        b'"id":"msg-branch-1","datasignature":"'
        + signature.encode()
        + b'","data":'
        + data
        + b"}"
    )
    return body, data


def _utf16_envelope_smuggling_undecodable_data() -> bytes:
    """A valid UTF-16 envelope whose raw bytes ALSO spell an ASCII
    ``"data"`` object holding bytes that are not UTF-8.

    ``json.loads`` sniffs the encoding of bytes, so the envelope parses;
    the signed-data extractor walks the raw bytes and finds the smuggled
    object instead, whose reparse is then not decodable as UTF-8.
    """
    smuggled = b' "data":{"a":"\xff\xfe"}'.decode("utf-16-le")
    envelope = {
        "specversion": "1.0",
        "type": "gr.boxnow.parcel_event_change",
        "id": "msg-utf16",
        "datasignature": "0" * 64,
        "note": smuggled,
    }
    return json.dumps(envelope, ensure_ascii=False).encode("utf-16-le")


def _tenant(slug: str, **fields) -> Tenant:
    tenant = Tenant(
        schema_name=slug.replace("-", "_"),
        name=slug,
        slug=slug,
        owner_email=f"owner-{slug}@example.com",
        box_now_webhook_secret=_SECRET,
        **fields,
    )
    tenant.auto_create_schema = False
    tenant.save()
    return tenant


@pytest.fixture
def entered_schemas():
    """Record which tenant schemas the resolver searches.

    The shipment lives in the test database's public schema, so every
    schema the resolver enters "finds" it — which tenants it is willing
    to enter is exactly what these tests pin.
    """
    entered: list[str] = []

    @contextmanager
    def _recording_schema_context(schema_name):
        entered.append(schema_name)
        yield

    @contextmanager
    def _tenant_context(tenant):
        previous = getattr(connection, "tenant", None)
        connection.set_tenant(tenant)
        try:
            yield
        finally:
            if previous is not None:
                connection.set_tenant(previous)
            else:
                connection.set_schema_to_public()

    # Only the tenants each test creates may be candidates.
    Tenant.objects.exclude(schema_name="public").update(is_active=False)
    with (
        patch(
            "shipping_boxnow.views.webhook.schema_context",
            _recording_schema_context,
        ),
        patch("shipping_boxnow.views.webhook.tenant_context", _tenant_context),
    ):
        yield entered


@pytest.fixture
def apply_async():
    with patch(
        "shipping_boxnow.tasks.process_boxnow_webhook_event.apply_async"
    ) as mocked:
        yield mocked


def _post(body: bytes):
    return APIClient().post(
        reverse("boxnow-webhook"), data=body, content_type="application/json"
    )


class TestTenantResolution:
    def test_inactive_and_suspended_owners_are_never_searched(
        self, entered_schemas, apply_async
    ):
        shipment = BoxNowShipmentFactory(with_parcel=True)
        _tenant("bn-inactive", is_active=False)
        _tenant(
            "bn-suspended",
            is_active=True,
            suspended_at=timezone.now() - timedelta(days=1),
        )
        body, _ = _signed_body(shipment.parcel_id)

        response = _post(body)

        # Orphan: acknowledged so BoxNow stops retrying, nothing queued.
        assert response.status_code == 200
        assert entered_schemas == []
        apply_async.assert_not_called()

    def test_active_owner_is_found_and_the_event_is_dispatched_to_it(
        self, entered_schemas, apply_async
    ):
        shipment = BoxNowShipmentFactory(with_parcel=True)
        _tenant("bn-inactive-2", is_active=False)
        _tenant("bn-active", is_active=True)
        body, data = _signed_body(shipment.parcel_id)

        response = _post(body)

        assert response.status_code == 200
        assert entered_schemas == ["bn_active"]
        apply_async.assert_called_once()
        kwargs = apply_async.call_args.kwargs
        assert kwargs["headers"] == {"_schema_name": "bn_active"}
        (envelope,) = kwargs["args"]
        assert envelope["data"] == json.loads(data)
        assert envelope["_data_fingerprint"] == (
            hashlib.sha256(data).hexdigest()
        )


class TestDispatchAndParsing:
    def test_broker_failure_returns_500_so_boxnow_retries(
        self, entered_schemas, apply_async
    ):
        shipment = BoxNowShipmentFactory(with_parcel=True)
        _tenant("bn-broker", is_active=True)
        apply_async.side_effect = OperationalError("broker unreachable")
        body, _ = _signed_body(shipment.parcel_id)

        response = _post(body)

        assert response.status_code == 500
        apply_async.assert_called_once()

    def test_signed_data_that_does_not_parse_is_rejected_with_400(
        self, entered_schemas, apply_async
    ):
        # extract_data_substring always yields a parseable object for a
        # body that itself parsed, so the defensive branch is reached by
        # handing the view an unparseable signed slice.
        body, _ = _signed_body("9200000999")

        with patch(
            "shipping_boxnow.views.webhook.extract_data_substring",
            return_value=b'{"parcelId": ',
        ):
            response = _post(body)

        assert response.status_code == 400
        assert entered_schemas == []
        apply_async.assert_not_called()

    @pytest.mark.parametrize(
        "body",
        [
            b"[]",
            b'"x"',
            b"42",
            b"null",
            b'{"data":{"a":1},"id":"\xff"}',
            b"\xff\xfe\x00",
            b"[" * 100_000,
            _utf16_envelope_smuggling_undecodable_data(),
        ],
        ids=[
            "list",
            "string",
            "number",
            "null",
            "invalid-utf8-in-object",
            "undecodable-bytes",
            "nested-too-deep",
            "utf16-envelope-smuggling-data",
        ],
    )
    def test_malformed_body_is_rejected_with_400(
        self, entered_schemas, apply_async, body
    ):
        response = _post(body)

        assert response.status_code == 400
        assert entered_schemas == []
        apply_async.assert_not_called()
