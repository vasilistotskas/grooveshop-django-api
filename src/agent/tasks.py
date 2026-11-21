from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from agent import outbox
from agent.models import AgentOrderEvent, AgentOrderEventStatus
from core import celery_app
from core.monitored_task import MonitoredTask
from core.retention import delete_in_batches
from core.retry import TRANSIENT_DB_ERRORS

logger = logging.getLogger(__name__)


# Retries cover the database only. A failed delivery is not an error of
# the task: ``agent.outbox`` records it on the row and the sweep below
# attempts it again on the row's own schedule.
@celery_app.task(
    base=MonitoredTask,
    autoretry_for=TRANSIENT_DB_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def deliver_agent_order_event(event_id: str) -> str:
    """The first attempt of an event, queued once its change committed."""
    return outbox.attempt(event_id)


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=TRANSIENT_DB_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def deliver_due_agent_order_events() -> dict[str, int]:
    """Attempt this store's due events. Runs per tenant through
    ``tenant.tasks.fanout_deliver_due_agent_order_events``."""
    return outbox.deliver_due()


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=TRANSIENT_DB_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def purge_expired_agent_order_events() -> dict[str, int]:
    """Delete delivered and failed events finished more than
    ``AGENT_ORDER_EVENT_RETENTION_DAYS`` ago. A pending event is kept.

    Runs per tenant through
    ``tenant.tasks.fanout_purge_expired_agent_order_events``.
    """
    cutoff = timezone.now() - timedelta(
        days=settings.AGENT_ORDER_EVENT_RETENTION_DAYS
    )
    deleted = delete_in_batches(
        AgentOrderEvent.objects.filter(
            status__in=(
                AgentOrderEventStatus.DELIVERED,
                AgentOrderEventStatus.FAILED,
            ),
            finished_at__lt=cutoff,
        )
    )
    if deleted:
        logger.info("Purged %s agent order events", deleted)
    return {"deleted": deleted}
