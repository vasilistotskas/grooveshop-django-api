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
import threading
import time
from collections.abc import Mapping
from datetime import UTC, timedelta
from email.utils import parsedate_to_datetime
from enum import StrEnum
from http import HTTPStatus
from uuid import UUID, uuid4

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
from tenant.membership import bound_tenant
from tenant.models import Tenant

logger = logging.getLogger(__name__)

#: The gateway's route for order events (``internal/server/routes.go``).
ORDER_EVENTS_PATH = "/internal/events/order-status"


#: ``last_error`` of an event whose order is deleted, soft or for good.
ORDER_DELETED = "order deleted"


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
    #: Not attempted: the gateway cannot resolve the store
    #: (``_store_is_active``). Attempted once it is active again.
    STORE_INACTIVE = "store_inactive"


#: Outcomes that would be the same for every other row: a sweep run stops.
STOPS_THE_RUN = frozenset(
    {Outcome.UNREACHABLE, Outcome.MISCONFIGURED, Outcome.STORE_INACTIVE}
)


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

    The row is claimed first: its next attempt moves to the time a failure
    would set, so a second worker finds it not due and a worker that dies
    mid-call leaves it to be attempted then, and ``claim`` gets a token
    that every later write of this attempt names (``_settle``).
    """
    if not settings.AGENT_GATEWAY_INTERNAL_URL:
        return Outcome.SKIPPED
    if not _store_is_active():
        return Outcome.STORE_INACTIVE
    event = _due().filter(pk=event_id).first()
    if event is None:
        return Outcome.SKIPPED
    now = timezone.now()
    number = event.attempts + 1
    token = uuid4()
    claimed = AgentOrderEvent.objects.filter(
        pk=event.pk,
        status=AgentOrderEventStatus.PENDING,
        attempts=event.attempts,
        next_attempt_at=event.next_attempt_at,
    ).update(
        attempts=number,
        last_attempt_at=now,
        next_attempt_at=now + retry_delay(number),
        claim=token,
    )
    if not claimed:
        return Outcome.SKIPPED
    if number > settings.AGENT_ORDER_EVENT_MAX_ATTEMPTS:
        # Only a worker that died during the last attempt leaves this.
        return _give_up(event, token, event.last_error, Outcome.FAILED)

    order = _order_state(event.order_id)
    if order is None:
        return _give_up(event, token, ORDER_DELETED, Outcome.FAILED)

    try:
        status, headers = _post(_body(event, order))
    except (requests.RequestException, TimeoutError) as error:
        return _failed(
            event, token, number, type(error).__name__, Outcome.UNREACHABLE
        )

    if 200 <= status < 300:
        if _settle(
            event,
            token,
            status=AgentOrderEventStatus.DELIVERED,
            finished_at=timezone.now(),
            last_error="",
        ):
            return Outcome.DELIVERED
        return Outcome.SKIPPED
    error = f"HTTP {status}"
    if status in DEPLOYMENT_FAULTS:
        return _failed(event, token, number, error, Outcome.MISCONFIGURED)
    if is_permanent_http_status(status):
        return _give_up(event, token, error, Outcome.FAILED)
    return _failed(
        event,
        token,
        number,
        error,
        Outcome.RETRY,
        retry_after=_retry_after(headers),
    )


def _post(body: dict[str, str]) -> tuple[int, Mapping[str, str]]:
    """POST an event and return the answer's status and headers, within
    connect + read timeouts in total.

    requests' read timeout bounds each wait for the next bytes, not the
    whole answer (urllib3's ``Timeout`` documents the same of ``total``),
    so a gateway answering a byte at a time could hold a sweep run past
    its budget. The call runs in a daemon thread that is abandoned once
    the deadline passes, and the body is never read: the status and
    ``Retry-After`` are the answer. An abandoned thread ends only when the
    gateway stops sending or closes the connection: bytes arriving within
    the read timeout keep it alive. Being a daemon, it dies with the
    worker child, which ``CELERY_WORKER_MAX_TASKS_PER_CHILD`` replaces; a
    run leaves at most one, since it stops on ``UNREACHABLE``.
    Raises ``TimeoutError`` at the deadline, and what the call raises.
    """
    connect = settings.AGENT_ORDER_EVENT_CONNECT_TIMEOUT
    read = settings.AGENT_ORDER_EVENT_READ_TIMEOUT
    answer: list[tuple[int, Mapping[str, str]]] = []
    failure: list[BaseException] = []

    def call() -> None:
        try:
            response = requests.post(
                f"{settings.AGENT_GATEWAY_INTERNAL_URL.rstrip('/')}"
                f"{ORDER_EVENTS_PATH}",
                json=body,
                headers={
                    "X-Internal-Token": settings.AGENT_GATEWAY_INTERNAL_SECRET
                },
                timeout=(connect, read),
                stream=True,
            )
            try:
                answer.append((response.status_code, response.headers))
            finally:
                response.close()
        except BaseException as error:
            failure.append(error)

    worker = threading.Thread(
        target=call, name="agent-order-event", daemon=True
    )
    worker.start()
    worker.join(connect + read)
    if failure:
        raise failure[0]
    if not answer:
        raise TimeoutError(f"no answer within {connect + read} s")
    return answer[0]


def _store_is_active() -> bool:
    """Whether the gateway can resolve this store: ``tenant/resolve``
    answers only a store whose row is active (``tenant.views``), so the
    events of an inactive store, or of a schema with no store row, could
    be neither rendered nor signed. They stay ``pending``, with no
    attempt spent, and go out once the store is active again.

    Read from the database at every attempt: a sweep run binds its store
    once, and the store can be suspended while the run goes on."""
    tenant = bound_tenant()
    return (
        tenant is not None
        and Tenant.objects.filter(pk=tenant.pk, is_active=True).exists()
    )


def _order_state(order_id: int) -> dict | None:
    """The order as the gateway is told of it, or None once it is deleted.

    The gateway renders the event from its own read of the order
    (``agent.views.AgentInternalOrderView``), which finds no soft-deleted
    order, so a soft-deleted order's event could never be rendered: it
    fails at once, like one whose order was deleted for good between the
    claim and this read (a deleted order otherwise takes its events with
    it)."""
    return (
        Order.objects.filter(pk=order_id)
        .values(
            "uuid",
            "status",
            "payment_status",
            "tracking_number",
            "agent_checkout",
        )
        .first()
    )


def _retry_after(headers: Mapping[str, str]) -> timedelta | None:
    """The wait a ``Retry-After`` header asks for (delta-seconds or an
    HTTP date), at least ``AGENT_ORDER_EVENT_RETRY_AFTER_MIN_SECONDS`` and
    at most ``AGENT_ORDER_EVENT_RETRY_AFTER_CAP_SECONDS``; None without
    a usable one."""
    value = headers.get("Retry-After", "").strip()
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
        # The checkout that placed the order; empty for an order placed
        # before the order carried it.
        "agentCheckoutId": str(order["agent_checkout"] or ""),
    }


def _settle(event: AgentOrderEvent, token: UUID, **fields: object) -> bool:
    """Write an attempt's result, if the attempt still holds the row. A
    manual retry clears the claim, so a superseded attempt writes nothing
    over the fresh cycle."""
    return bool(
        AgentOrderEvent.objects.filter(pk=event.pk, claim=token).update(
            **fields
        )
    )


def _failed(
    event: AgentOrderEvent,
    token: UUID,
    number: int,
    error: str,
    outcome: Outcome,
    *,
    retry_after: timedelta | None = None,
) -> Outcome:
    """A failed attempt a retry can fix. The claim already set the next
    attempt by the backoff; a ``Retry-After`` replaces it. The last
    attempt gives up instead, whatever the failure, and still reports a
    gateway-wide fault so the sweep run stops."""
    if number >= settings.AGENT_ORDER_EVENT_MAX_ATTEMPTS:
        terminal = outcome if outcome in STOPS_THE_RUN else Outcome.FAILED
        return _give_up(event, token, error, terminal)
    fields: dict[str, object] = {"last_error": error}
    if retry_after is not None:
        fields["next_attempt_at"] = timezone.now() + retry_after
    if not _settle(event, token, **fields):
        return Outcome.SKIPPED
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


def _give_up(
    event: AgentOrderEvent, token: UUID, error: str, outcome: Outcome
) -> Outcome:
    """Make the row ``failed``. Returns ``outcome``: ``FAILED``, or the
    gateway-wide fault of the last attempt, which stops the sweep run."""
    if not _settle(
        event,
        token,
        status=AgentOrderEventStatus.FAILED,
        finished_at=timezone.now(),
        last_error=error,
    ):
        return Outcome.SKIPPED
    logger.error(
        "Agent order event %s (order %s) failed for good: %s",
        event.pk,
        event.order_id,
        error,
    )
    return outcome


def deliver_due() -> dict[str, int]:
    """Attempt every due event of this store, within a batch and a time
    budget per run.

    Rows are taken one at a time, so an event that waited behind an
    earlier one of its order goes out in the same run once that one is
    delivered. A run takes no new row once it has spent
    ``AGENT_ORDER_EVENT_SWEEP_BUDGET_SECONDS``, so slow answers cannot
    stack runs, and stops when the gateway cannot be reached or refuses
    the call itself, or the store is no longer active: every other row
    would get the same answer.
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

    Each row is reset by an update that itself refuses a delivered row,
    so one delivered after the selection is left as it is, and only the
    rows reset are queued. The reset clears the claim: an attempt still in
    flight writes nothing over the new cycle (``_settle``). An event
    whose order is deleted is made ``failed`` instead (``_order_state``
    says why), and is not queued. Returns how many were queued.
    """
    order_exists = Exists(Order.objects.filter(pk=OuterRef("order_id")))
    queued = 0
    for event_id in list(events.values_list("pk", flat=True)):
        not_delivered = AgentOrderEvent.objects.filter(pk=event_id).exclude(
            status=AgentOrderEventStatus.DELIVERED
        )
        not_delivered.exclude(order_exists).update(
            status=AgentOrderEventStatus.FAILED,
            finished_at=timezone.now(),
            last_error=ORDER_DELETED,
            claim=None,
        )
        reset = not_delivered.filter(order_exists).update(
            status=AgentOrderEventStatus.PENDING,
            attempts=0,
            next_attempt_at=timezone.now(),
            finished_at=None,
            claim=None,
        )
        if reset:
            _queue_attempt(event_id)
            queued += 1
    return queued
