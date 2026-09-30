"""``NotificationConsumer.send_notification`` encodes with
``DjangoJSONEncoder``: an event carrying a Decimal, datetime or UUID
must reach the socket instead of raising ``TypeError``."""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from notification.consumers import NotificationConsumer


@pytest.mark.asyncio
async def test_send_notification_serializes_non_json_types():
    consumer = NotificationConsumer()
    sent_payloads: list[str] = []

    async def fake_send(text_data=None, bytes_data=None):
        sent_payloads.append(text_data)

    consumer.send = fake_send  # type: ignore[method-assign]

    await consumer.send_notification(
        {
            "type": "send_notification",
            "amount": Decimal("19.99"),
            "created_at": datetime(2026, 1, 15, 10, 30, tzinfo=UTC),
            "notification_uuid": uuid.UUID(
                "12345678-1234-5678-1234-567812345678"
            ),
            "user": 7,
            "seen": False,
        }
    )

    assert [json.loads(p) for p in sent_payloads] == [
        {
            "type": "send_notification",
            "amount": "19.99",
            "created_at": "2026-01-15T10:30:00Z",
            "notification_uuid": "12345678-1234-5678-1234-567812345678",
            "user": 7,
            "seen": False,
        }
    ]
