"""Celery tasks for dispatching Meta Conversions API events.

Why every CAPI dispatch is async:
* The HTTP round-trip to graph.facebook.com is 100-300ms even on a
  warm path. Tying that to the request thread (or worse, a webhook
  handler that already has a Stripe HTTP timeout breathing down its
  neck) is asking for tail-latency pain.
* Meta's API has occasional 5xx blips and rate-limit responses. The
  Celery retry-with-backoff pattern handles both cleanly.
* On startup with bad credentials we log + skip rather than crash.

Idempotency: each task is keyed by ``event_id``. If a row in
``MetaCapiEventLog`` with that id is already ``SENT`` we short-circuit.
This makes Stripe webhook redeliveries safe — a retried
``payment_intent.succeeded`` lands the same event_id again and we
just record the second attempt as ``SKIPPED``.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from celery import shared_task
from django.db import transaction

from core.tasks import MonitoredTask
from meta_capi.client import MetaCapiClient
from meta_capi.events import StandardEvent
from meta_capi.exceptions import (
    MetaCapiConfigError,
    MetaCapiError,
    MetaCapiTransientError,
)
from meta_capi.models import MetaCapiEventLog, MetaCapiEventStatus
from meta_capi.services import (
    build_complete_registration_event,
    build_initiate_checkout_event,
    build_purchase_event,
    build_refund_event,
    is_capi_enabled,
    should_dispatch_for_order,
)

logger = logging.getLogger(__name__)


def _serialize_event_payload(event: Any) -> dict[str, Any]:
    """SDK Events expose ``normalize`` which returns a dict ready for
    JSON. We keep the result on ``MetaCapiEventLog.payload`` for
    incident replay — by this point all PII is already SHA-256
    hashed by the SDK, so storing it is fine.
    """
    try:
        normalized = event.normalize()
    except Exception:  # pragma: no cover — defensive
        return {}
    if isinstance(normalized, dict):
        return normalized
    return {}


def _dispatch(
    *,
    event: Any,
    event_id: str,
    event_name: str,
    order_id: int | None,
    user_id: int | None,
) -> None:
    """Send one event and persist the audit row.

    Raises MetaCapiTransientError so Celery's autoretry kicks in;
    permanent MetaCapiError surfaces as a logged failure with a
    FAILED log row (no retry).
    """
    # Idempotency check — if the row exists and is SENT, we're done.
    existing = MetaCapiEventLog.objects.filter(event_id=event_id).first()
    if existing and existing.status == MetaCapiEventStatus.SENT:
        logger.info(
            "meta_capi: event %s already SENT (log=%s), skipping",
            event_id,
            existing.id,
        )
        return

    log_row, _ = MetaCapiEventLog.objects.update_or_create(
        event_id=event_id,
        defaults={
            "event_name": event_name,
            "order_id": order_id,
            "user_id": user_id,
            "status": MetaCapiEventStatus.PENDING,
            "payload": _serialize_event_payload(event),
        },
    )

    try:
        response = MetaCapiClient().send([event])
    except MetaCapiTransientError as exc:
        log_row.status = MetaCapiEventStatus.FAILED
        log_row.error_message = str(exc)[:1000]
        log_row.save(update_fields=["status", "error_message", "updated_at"])
        # Re-raise so Celery autoretries. The next attempt will flip
        # the row back to PENDING via update_or_create above.
        raise
    except (MetaCapiError, MetaCapiConfigError) as exc:
        log_row.status = MetaCapiEventStatus.FAILED
        log_row.error_message = str(exc)[:1000]
        log_row.save(update_fields=["status", "error_message", "updated_at"])
        logger.error("meta_capi: permanent failure for %s: %s", event_id, exc)
        return

    log_row.status = MetaCapiEventStatus.SENT
    log_row.fbtrace_id = response.fbtrace_id[:128]
    log_row.events_received = response.events_received
    log_row.error_message = ""
    log_row.save(
        update_fields=[
            "status",
            "fbtrace_id",
            "events_received",
            "error_message",
            "updated_at",
        ]
    )


@shared_task(
    base=MonitoredTask,
    bind=True,
    autoretry_for=(MetaCapiTransientError,),
    retry_backoff=10,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
    ignore_result=True,
)
def dispatch_purchase_event(self, order_id: int) -> None:
    """Send Purchase to Meta for ``order_id``.

    Wired to ``order_paid`` signal via on_commit so the order is
    guaranteed visible by the time this runs.
    """
    from order.models.order import Order

    if not is_capi_enabled():
        logger.debug(
            "meta_capi: disabled, skipping Purchase for order %s", order_id
        )
        return

    try:
        order = Order.objects.select_related("user", "country").get(pk=order_id)
    except Order.DoesNotExist:
        logger.warning(
            "meta_capi: order %s vanished before Purchase dispatch", order_id
        )
        return

    if not should_dispatch_for_order(order):
        logger.info(
            "meta_capi: ad consent not granted for order %s, skipping Purchase",
            order_id,
        )
        return

    event, event_id = build_purchase_event(order)
    _dispatch(
        event=event,
        event_id=event_id,
        event_name=str(StandardEvent.PURCHASE),
        order_id=order.id,
        user_id=order.user_id,
    )


@shared_task(
    base=MonitoredTask,
    bind=True,
    autoretry_for=(MetaCapiTransientError,),
    retry_backoff=10,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
    ignore_result=True,
)
def dispatch_initiate_checkout_event(self, order_id: int) -> None:
    from order.models.order import Order

    if not is_capi_enabled():
        return

    try:
        order = Order.objects.select_related("user", "country").get(pk=order_id)
    except Order.DoesNotExist:
        return

    if not should_dispatch_for_order(order):
        return

    event, event_id = build_initiate_checkout_event(order)
    _dispatch(
        event=event,
        event_id=event_id,
        event_name=str(StandardEvent.INITIATE_CHECKOUT),
        order_id=order.id,
        user_id=order.user_id,
    )


@shared_task(
    base=MonitoredTask,
    bind=True,
    autoretry_for=(MetaCapiTransientError,),
    retry_backoff=10,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
    ignore_result=True,
)
def dispatch_refund_event(
    self, order_id: int, amount: str | None = None
) -> None:
    from order.models.order import Order

    if not is_capi_enabled():
        return

    try:
        order = Order.objects.select_related("user", "country").get(pk=order_id)
    except Order.DoesNotExist:
        return

    if not should_dispatch_for_order(order):
        return

    refund_amount = Decimal(amount) if amount else None
    event, event_id = build_refund_event(order, refund_amount)
    _dispatch(
        event=event,
        event_id=event_id,
        event_name="Refund",
        order_id=order.id,
        user_id=order.user_id,
    )


@shared_task(
    base=MonitoredTask,
    bind=True,
    autoretry_for=(MetaCapiTransientError,),
    retry_backoff=10,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
    ignore_result=True,
)
def dispatch_complete_registration_event(
    self,
    user_id: int,
    *,
    fbp: str | None = None,
    fbc: str | None = None,
    client_ip_address: str | None = None,
    client_user_agent: str | None = None,
    event_id: str | None = None,
    event_source_url: str | None = None,
) -> None:
    from user.models.account import UserAccount

    if not is_capi_enabled():
        return

    try:
        user = UserAccount.objects.get(pk=user_id)
    except UserAccount.DoesNotExist:
        return

    event, eid = build_complete_registration_event(
        user,
        fbp=fbp,
        fbc=fbc,
        client_ip_address=client_ip_address,
        client_user_agent=client_user_agent,
        event_id=event_id,
        event_source_url=event_source_url,
    )
    _dispatch(
        event=event,
        event_id=eid,
        event_name=str(StandardEvent.COMPLETE_REGISTRATION),
        order_id=None,
        user_id=user.id,
    )


def schedule_purchase(order_id: int) -> None:
    """Convenience wrapper: schedule on commit, no-op if Celery is
    misconfigured. Used by signal handlers so they don't have to
    repeat the on_commit boilerplate.
    """
    transaction.on_commit(
        lambda oid=order_id: dispatch_purchase_event.delay(oid)
    )


def schedule_initiate_checkout(order_id: int) -> None:
    transaction.on_commit(
        lambda oid=order_id: dispatch_initiate_checkout_event.delay(oid)
    )


def schedule_refund(order_id: int, amount: Decimal | None) -> None:
    amount_str = str(amount) if amount is not None else None
    transaction.on_commit(
        lambda oid=order_id, amt=amount_str: dispatch_refund_event.delay(
            oid, amt
        )
    )
