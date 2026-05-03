"""Tests for R4-D3: NotificationConsumer uses DjangoJSONEncoder.

Verifies that send_notification can serialize event dicts containing
Decimal, datetime, and UUID values without raising TypeError.
"""

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_send_notification_serializes_decimal():
    """DjangoJSONEncoder handles Decimal values in notification events."""
    from notification.consumers import NotificationConsumer

    consumer = NotificationConsumer()
    consumer.channel_layer = None
    consumer.channel_name = "test"

    sent_payloads: list[str] = []

    async def fake_send(text_data=None, bytes_data=None):
        sent_payloads.append(text_data)

    consumer.send = fake_send  # type: ignore[method-assign]

    event = {
        "type": "send_notification",
        "amount": Decimal("19.99"),
        "user": 42,
    }
    await consumer.send_notification(event)

    assert len(sent_payloads) == 1
    parsed = json.loads(sent_payloads[0])
    # DjangoJSONEncoder renders Decimal as a float string — it must not raise
    assert "amount" in parsed


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_send_notification_serializes_datetime():
    """DjangoJSONEncoder handles datetime values in notification events."""
    from notification.consumers import NotificationConsumer

    consumer = NotificationConsumer()

    sent_payloads: list[str] = []

    async def fake_send(text_data=None, bytes_data=None):
        sent_payloads.append(text_data)

    consumer.send = fake_send  # type: ignore[method-assign]

    dt = datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    event = {
        "type": "send_notification",
        "created_at": dt,
        "user": 1,
    }
    await consumer.send_notification(event)

    assert len(sent_payloads) == 1
    parsed = json.loads(sent_payloads[0])
    assert "created_at" in parsed
    # DjangoJSONEncoder serializes datetime as ISO 8601 string
    assert "2026-01-15" in parsed["created_at"]


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_send_notification_serializes_uuid():
    """DjangoJSONEncoder handles UUID values in notification events."""
    from notification.consumers import NotificationConsumer

    consumer = NotificationConsumer()

    sent_payloads: list[str] = []

    async def fake_send(text_data=None, bytes_data=None):
        sent_payloads.append(text_data)

    consumer.send = fake_send  # type: ignore[method-assign]

    uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    event = {
        "type": "send_notification",
        "notification_uuid": uid,
        "user": 1,
    }
    await consumer.send_notification(event)

    assert len(sent_payloads) == 1
    parsed = json.loads(sent_payloads[0])
    assert parsed["notification_uuid"] == str(uid)


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_send_notification_plain_dict_still_works():
    """Normal string/int/bool dicts serialize without errors."""
    from notification.consumers import NotificationConsumer

    consumer = NotificationConsumer()

    sent_payloads: list[str] = []

    async def fake_send(text_data=None, bytes_data=None):
        sent_payloads.append(text_data)

    consumer.send = fake_send  # type: ignore[method-assign]

    event = {
        "type": "send_notification",
        "user": 7,
        "seen": False,
        "kind": "info",
        "link": "/orders/42",
    }
    await consumer.send_notification(event)

    assert len(sent_payloads) == 1
    parsed = json.loads(sent_payloads[0])
    assert parsed["user"] == 7
    assert parsed["seen"] is False
