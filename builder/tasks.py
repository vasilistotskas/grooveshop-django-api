import logging

from builder import scheduling
from core import celery_app
from core.retry import TRANSIENT_ERRORS
from core.tasks import MonitoredTask

logger = logging.getLogger(__name__)


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def run_due_publishes(self) -> dict[str, int]:
    """Every minute, per store (``tenant.tasks.fanout_run_due_builder_
    publishes``): release the scheduled publishes whose time has come.

    Idempotent: a schedule is spent inside the transaction that releases
    it, so a retry, an overlapping sweep or a second worker finds nothing
    to do (``builder.scheduling.execute``).
    """
    return scheduling.run_due()
