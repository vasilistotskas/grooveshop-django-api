"""The email log's retention sweep."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from core import celery_app
from core.retention import delete_in_batches
from core.retry import TRANSIENT_DB_ERRORS
from core.store_settings import store_setting
from core.tasks import MonitoredTask
from emails.events import EmailLogNotFound, route
from emails.models import EmailLog

logger = logging.getLogger(__name__)


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_DB_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def purge_expired_email_logs() -> dict[str, int]:
    """Delete ``EmailLog`` rows older than ``EMAIL_LOG_RETENTION_DAYS``.

    The log of the ACTIVE schema: a store's through
    ``tenant.tasks.fanout_purge_expired_email_logs`` with that store's
    setting, the platform's own in public with the setting stored there.
    ``0`` keeps every row. Events go with their row (cascade); suppressions
    are protective records and stay until removed.
    """
    days = int(store_setting("EMAIL_LOG_RETENTION_DAYS"))
    if days <= 0:
        return {"deleted": 0}
    cutoff = timezone.now() - timedelta(days=days)
    deleted = delete_in_batches(EmailLog.objects.filter(created_at__lt=cutoff))
    if deleted:
        logger.info(
            "Purged %s email log rows older than %s days", deleted, days
        )
    return {"deleted": deleted}


@celery_app.task(
    base=MonitoredTask,
    max_retries=4,
    autoretry_for=(EmailLogNotFound, *TRANSIENT_DB_ERRORS),
    retry_backoff=5,
    retry_jitter=True,
)
def process_provider_event(svix_id: str, payload: dict) -> bool:
    """Apply one verified provider event (``emails.events.route``).

    Runs in the schema of the host that received it. A row that is not there
    yet is retried a few times, because a fast event can beat the send's
    write of the provider id; one that never appears (purged, or foreign)
    ends as a recorded task failure.
    """
    return route(svix_id, payload)
