"""The agent gateway's order events: a state row per event and a sweep.

An event of an order an agent placed is written as an ``AgentOrderEvent``
in the transaction of the change, its first attempt is queued for after
the commit, and ``deliver_due`` (beat, per store) retries what is due.
An order's events go out in the order they happened: one waits while an
earlier event of its order is still pending. Read
``docs/agent-order-events.md``.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import UTC, timedelta
from email.utils import parsedate_to_datetime
from enum import StrEnum
from http import HTTPStatus
from uuid import UUID

import requests
from django.conf import settings
from django.db import connection
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from agent.models import (
    AgentOrderEvent,
    AgentOrderEventStatus,
    AgentOrderEventType,
)
from core.retry import is_permanent_http_status
from order.enum.attribution import OrderSourceType
from order.models.attribution import OrderAttribution
from order.models.order import Order
from tenant.celery import dispatch_on_commit

logger = logging.getLogger(__name__)

#: The gateway's route for order events (``internal/server/routes.go``).
ORDER_EVENTS_PATH = "/internal/events/order-status"


#: Answers about the deployment, not the event: a wrong shared secret (one
#: being rotated, say) or a wrong gateway URL. Fixing the deployment fixes
#: them, so the event is retried like an outage, never failed for them.
DEPLOYMENT_FAULTS = frozenset(
    {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND}
)


class Outcome(StrEnum):
    """What one call to ``attempt`` did."""

    #: Not attempted: not due, claimed by another worker, or waiting
    #: behind an earlier event of its order.
    SKIPPED = "skipped"
    DELIVERED = "delivered"
    #: Answered with a status a retry can change: attempted again later.
    RETRY = "retry"
    #: The gateway could not be reached: attempted again later.
    UNREACHABLE = "unreachable"
    #: The gateway refused the call itself (``DEPLOYMENT_FAULTS``):
    #: attempted again later.
    MISCONFIGURED = "misconfigured"
    #: Refused for good, or out of attempts.
    FAILED = "failed"


#: Outcomes that would be the same for every other row: a sweep run stops.
STOPS_THE_RUN = frozenset({Outcome.UNREACHABLE, Outcome.MISCONFIGURED})


def record_order_created(order_id: int) -> None:
    """Record that an agent placed this order, in its creation transaction."""
    _record(order_id, AgentOrderEventType.CREATED)


def record_order_changed(order_id: int) -> None:
    """Record a change of the order's status, payment status or tracking,
    when an agent placed it."""
    if not settings.AGENT_GATEWAY_INTERNAL_URL:
        return
    placed_by_an_agent = OrderAttribution.objects.filter(
        order_id=order_id, source_type=OrderSourceType.AGENT
    ).exists()
    if placed_by_an_agent:
        _record(order_id, AgentOrderEventType.UPDATED)


def _record(order_id: int, event_type: AgentOrderEventType) -> None:
    # An unset gateway URL is a deployment without the agent surface.
    if not settings.AGENT_GATEWAY_INTERNAL_URL:
        return
    event = AgentOrderEvent.objects.create(
        order_id=order_id, event_type=event_type
    )
    _queue_attempt(event.pk)


def _queue_attempt(event_id: UUID) -> None:
    from agent.tasks import deliver_agent_order_event

    dispatch_on_commit(deliver_agent_order_event, [str(event_id)])


def retry_delay(attempt_number: int) -> timedelta:
    """How long the next attempt waits after attempt ``attempt_number``
    (1 is the first) failed: doubling from the base, capped, with jitter."""
    seconds = min(
        settings.AGENT_ORDER_EVENT_BACKOFF_SECONDS * 2 ** (attempt_number - 1),
        settings.AGENT_ORDER_EVENT_BACKOFF_CAP_SECONDS,
    )
    jitter = settings.AGENT_ORDER_EVENT_BACKOFF_JITTER
    return timedelta(seconds=seconds * random.uniform(1 - jitter, 1 + jitter))


def _behind_an_earlier_event() -> Exists:
    """Whether an earlier event of the same order is still pending."""
    return Exists(
        AgentOrderEvent.objects.filter(
            order_id=OuterRef("order_id"),
            status=AgentOrderEventStatus.PENDING,
        ).filter(
            Q(occurred_at__lt=OuterRef("occurred_at"))
            | Q(occurred_at=OuterRef("occurred_at"), pk__lt=OuterRef("pk"))
        )
    )


def _due() -> QuerySet[AgentOrderEvent]:
    return AgentOrderEvent.objects.filter(
        status=AgentOrderEventStatus.PENDING,
        next_attempt_at__lte=timezone.now(),
    ).exclude(_behind_an_earlier_event())


def attempt(event_id: UUID | str) -> Outcome:
    """Send one event to the gateway once, if it is due, and record the
    outcome on its row.

    The row is claimed first by moving its next attempt to the time a
    failure would set, so a second worker finds it not due, and a worker
    that dies mid-call leaves it to be attempted then.
    """
    if not settings.AGENT_GATEWAY_INTERNAL_URL:
        return Outcome.SKIPPED
    event = _due().filter(pk=event_id).first()
    if event is None:
        return Outcome.SKIPPED
    now = timezone.now()
    number = event.attempts + 1
    claimed = AgentOrderEvent.objects.filter(
        pk=event.pk,
        status=AgentOrderEventStatus.PENDING,
        attempts=event.attempts,
        next_attempt_at=event.next_attempt_at,
    ).update(
        attempts=number,
        last_attempt_at=now,
        next_attempt_at=now + retry_delay(number),
    )
    if not claimed:
        return Outcome.SKIPPED
    if number > settings.AGENT_ORDER_EVENT_MAX_ATTEMPTS:
        # Only a worker that died during the last attempt leaves this.
        return _give_up(event, event.last_error)

    order = _order_state(event.order_id)
    if order is None:
        # Deleted for good between the claim and this read. A deleted
        # order takes its events with it, so this row is normally gone
        # too; nothing is left to render either way.
        return _give_up(event, "order deleted")

    try:
        response = requests.post(
            f"{settings.AGENT_GATEWAY_INTERNAL_URL.rstrip('/')}"
            f"{ORDER_EVENTS_PATH}",
            json=_body(event, order),
            headers={
                "X-Internal-Token": settings.AGENT_GATEWAY_INTERNAL_SECRET
            },
            timeout=(
                settings.AGENT_ORDER_EVENT_CONNECT_TIMEOUT,
                settings.AGENT_ORDER_EVENT_READ_TIMEOUT,
            ),
        )
    except requests.RequestException as error:
        return _failed(event, number, type(error).__name__, Outcome.UNREACHABLE)

    status = response.status_code
    if 200 <= status < 300:
        AgentOrderEvent.objects.filter(pk=event.pk).update(
            status=AgentOrderEventStatus.DELIVERED,
            finished_at=timezone.now(),
            last_error="",
        )
        return Outcome.DELIVERED
    error = f"HTTP {status}"
    if status in DEPLOYMENT_FAULTS:
        return _failed(event, number, error, Outcome.MISCONFIGURED)
    if is_permanent_http_status(status):
        return _give_up(event, error)
    return _failed(
        event, number, error, Outcome.RETRY, retry_after=_retry_after(response)
    )


def _order_state(order_id: int) -> dict | None:
    """The order as the gateway is told of it, or None once it is deleted
    for good. A soft-deleted order is still the order the platform placed:
    its events are delivered."""
    return (
        Order.objects.all_with_deleted()
        .filter(pk=order_id)
        .values("uuid", "status", "payment_status", "tracking_number")
        .first()
    )


def _retry_after(response: requests.Response) -> timedelta | None:
    """The wait a ``Retry-After`` header asks for (delta-seconds or an
    HTTP date), at least ``AGENT_ORDER_EVENT_RETRY_AFTER_MIN_SECONDS`` and
    at most ``AGENT_ORDER_EVENT_RETRY_AFTER_CAP_SECONDS``; None without
    a usable one."""
    value = response.headers.get("Retry-After", "").strip()
    if not value:
        return None
    if value.isdigit():
        seconds = float(value)
    else:
        try:
            when = parsedate_to_datetime(value)
        except TypeError, ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - timezone.now()).total_seconds()
    return timedelta(
        seconds=min(
            max(seconds, settings.AGENT_ORDER_EVENT_RETRY_AFTER_MIN_SECONDS),
            settings.AGENT_ORDER_EVENT_RETRY_AFTER_CAP_SECONDS,
        )
    )


def _body(event: AgentOrderEvent, order: dict) -> dict[str, str]:
    return {
        "eventId": str(event.pk),
        "eventType": event.event_type,
        "occurredAt": event.occurred_at.isoformat(),
        # The task runs in the store's schema (``TenantTask``).
        "schemaName": connection.schema_name,
        "orderUuid": str(order["uuid"]),
        "status": order["status"],
        "paymentStatus": order["payment_status"],
        "trackingNumber": order["tracking_number"] or "",
    }


def _failed(
    event: AgentOrderEvent,
    number: int,
    error: str,
    outcome: Outcome,
    *,
    retry_after: timedelta | None = None,
) -> Outcome:
    """A failed attempt a retry can fix. The claim already set the next
    attempt by the backoff; a ``Retry-After`` replaces it. The last
    attempt gives up instead, whatever the failure."""
    if number >= settings.AGENT_ORDER_EVENT_MAX_ATTEMPTS:
        return _give_up(event, error)
    fields: dict[str, object] = {"last_error": error}
    if retry_after is not None:
        fields["next_attempt_at"] = timezone.now() + retry_after
    AgentOrderEvent.objects.filter(pk=event.pk).update(**fields)
    # A deployment fault is an operator's to fix: ERROR, once per run,
    # since the run stops on it (``deliver_due``).
    log = logger.error if outcome is Outcome.MISCONFIGURED else logger.warning
    log(
        "Agent order event %s (order %s) attempt %s failed: %s",
        event.pk,
        event.order_id,
        number,
        error,
    )
    return outcome


def _give_up(event: AgentOrderEvent, error: str) -> Outcome:
    AgentOrderEvent.objects.filter(pk=event.pk).update(
        status=AgentOrderEventStatus.FAILED,
        finished_at=timezone.now(),
        last_error=error,
    )
    logger.error(
        "Agent order event %s (order %s) failed for good: %s",
        event.pk,
        event.order_id,
        error,
    )
    return Outcome.FAILED


def deliver_due() -> dict[str, int]:
    """Attempt every due event of this store, within a batch and a time
    budget per run.

    Rows are taken one at a time, so an event that waited behind an
    earlier one of its order goes out in the same run once that one is
    delivered. A run takes no new row once it has spent
    ``AGENT_ORDER_EVENT_SWEEP_BUDGET_SECONDS``, so slow answers cannot
    stack runs, and stops when the gateway cannot be reached or refuses
    the call itself: every other row would get the same answer.
    """
    counts: dict[str, int] = {}
    if not settings.AGENT_GATEWAY_INTERNAL_URL:
        return counts
    started = time.monotonic()
    for _ in range(settings.AGENT_ORDER_EVENT_SWEEP_BATCH):
        if (
            time.monotonic() - started
            >= settings.AGENT_ORDER_EVENT_SWEEP_BUDGET_SECONDS
        ):
            break
        event_id = (
            _due()
            .order_by("next_attempt_at", "occurred_at")
            .values_list("pk", flat=True)
            .first()
        )
        if event_id is None:
            break
        outcome = attempt(event_id)
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome in STOPS_THE_RUN:
            break
    return counts


def retry_now(events: QuerySet[AgentOrderEvent]) -> int:
    """Send these events again now, with a fresh set of attempts.

    A delivered event is left as it is: the platform already has it.
    Returns how many were queued.
    """
    event_ids = list(
        events.exclude(status=AgentOrderEventStatus.DELIVERED).values_list(
            "pk", flat=True
        )
    )
    AgentOrderEvent.objects.filter(pk__in=event_ids).update(
        status=AgentOrderEventStatus.PENDING,
        attempts=0,
        next_attempt_at=timezone.now(),
        finished_at=None,
    )
    for event_id in event_ids:
        _queue_attempt(event_id)
    return len(event_ids)
