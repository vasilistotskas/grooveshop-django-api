"""Unit tests for order.payment_events."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from order.payment_events import (
    PAYMENT_STATUS_CHANNEL_PREFIX,
    payment_status_channel,
    publish_payment_status,
)


def test_payment_status_channel_format():
    assert payment_status_channel(42) == f"{PAYMENT_STATUS_CHANNEL_PREFIX}42"


@pytest.mark.django_db(transaction=True)
def test_publish_payment_status_fires_redis_publish_with_expected_payload():
    order = MagicMock()
    order.id = 17
    order.uuid = "3b3b3b3b-3b3b-3b3b-3b3b-3b3b3b3b3b3b"
    order.status = "PROCESSING"
    order.payment_status = "COMPLETED"
    order.payment_id = "pi_123"

    with (
        patch("order.payment_events.Redis") as redis_cls,
        patch(
            "order.payment_events.transaction.on_commit",
            side_effect=lambda cb: cb(),
        ),
    ):
        redis_cls.from_url.return_value.publish = MagicMock()
        publish_payment_status(order)

        redis_cls.from_url.return_value.publish.assert_called_once()
        channel, payload = redis_cls.from_url.return_value.publish.call_args[0]
        assert channel == payment_status_channel(17)
        decoded = json.loads(payload)
        assert decoded == {
            "orderId": 17,
            "orderUuid": "3b3b3b3b-3b3b-3b3b-3b3b-3b3b3b3b3b3b",
            "status": "PROCESSING",
            "paymentStatus": "COMPLETED",
            "paymentId": "pi_123",
        }


@pytest.mark.django_db(transaction=True)
def test_publish_payment_status_swallows_redis_errors():
    """A broken Redis must not propagate into the webhook handler."""
    order = MagicMock()
    order.id = 99
    order.uuid = None
    order.status = "PENDING"
    order.payment_status = "FAILED"
    order.payment_id = None

    with (
        patch(
            "order.payment_events.Redis",
            side_effect=RuntimeError("redis down"),
        ),
        patch(
            "order.payment_events.transaction.on_commit",
            side_effect=lambda cb: cb(),
        ),
    ):
        # Must not raise
        publish_payment_status(order)
