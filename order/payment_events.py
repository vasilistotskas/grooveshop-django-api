"""Publish payment-status transitions to Redis pub/sub.

The Nuxt SSE endpoint subscribes to `payment:status:{order_id}` and
forwards each message to the customer's browser in real time, replacing
the 2-second client-side poll loop.

Why pub/sub instead of polling from Nuxt:
- Stripe/Viva webhooks update payment status in bursts. A push model
  delivers the update within ms of the webhook firing rather than up
  to 2s later (poll interval) and up to 20s (max attempts).
- Zero idle load: no timer runs unless a checkout is actually in flight.

Failure mode: Redis publish failures are logged and swallowed — the
payment state itself is already persisted in Postgres, and the client
falls back to polling if SSE disconnects.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction

if TYPE_CHECKING:
    from order.models.order import Order

logger = logging.getLogger(__name__)

PAYMENT_STATUS_CHANNEL_PREFIX = "payment:status:"


def payment_status_channel(order_id: int) -> str:
    return f"{PAYMENT_STATUS_CHANNEL_PREFIX}{order_id}"


def _serialize(order: Order) -> str:
    payload = {
        "orderId": order.id,
        "orderUuid": str(order.uuid) if getattr(order, "uuid", None) else None,
        "status": order.status,
        "paymentStatus": order.payment_status,
        "paymentId": order.payment_id or None,
    }
    return json.dumps(payload)


def _publish(order_id: int, message: str) -> None:
    # Import lazily — the redis package is an install-time dep but we
    # don't want module-level import side effects (and it keeps test
    # isolation cleaner).
    try:
        from redis import Redis  # noqa: PLC0415

        client = Redis.from_url(settings.REDIS_URL)
        client.publish(payment_status_channel(order_id), message)
    except Exception as exc:  # pragma: no cover — defensive only
        # RedisError is the expected case; broader except guards against
        # the rare `django.core.exceptions.ImproperlyConfigured` at
        # import time if REDIS_URL is unset in a test environment.
        logger.warning(
            "payment_events publish failed for order %s: %s",
            order_id,
            exc,
        )


def publish_payment_status(order: Order) -> None:
    """Publish the order's current payment status to subscribers.

    Fires after the DB transaction commits so subscribers never read a
    stale snapshot. Safe to call from webhook handlers, signal
    receivers, or service methods — duplicate publishes are idempotent
    from the subscriber's perspective (same payload).
    """
    message = _serialize(order)
    transaction.on_commit(lambda: _publish(order.id, message))
