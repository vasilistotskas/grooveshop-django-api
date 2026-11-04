from __future__ import annotations

from core import celery_app
from core.retry import TRANSIENT_ERRORS, TRANSIENT_HTTP_ERRORS
from core.tasks import MonitoredTask
from indexnow.service import send_pending


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=(*TRANSIENT_ERRORS, *TRANSIENT_HTTP_ERRORS),
    max_retries=3,
    retry_backoff=60,
    retry_backoff_max=900,
    retry_jitter=True,
)
def submit_pending_urls() -> dict[str, int]:
    """Tell search engines which product and blog pages of this store
    changed since the last run, in one IndexNow POST (``indexnow.service``).
    """
    submission = send_pending()
    return {"paths": submission.paths, "urls": submission.urls}
