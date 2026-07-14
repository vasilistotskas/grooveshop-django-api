import logging

from core import celery_app
from core.tasks import MonitoredTask

logger = logging.getLogger(__name__)


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def save_search_query(
    query: str,
    language_code: str | None,
    content_type: str,
    results_count: int,
    estimated_total_hits: int,
    processing_time_ms: int | None,
    user_id: int | None,
    session_key: str | None,
    ip_address: str | None,
    user_agent: str,
) -> None:
    """Persist a SearchQuery analytics record asynchronously."""
    from search.models import SearchQuery

    user = None
    if user_id is not None:
        from django.contrib.auth import get_user_model

        User = get_user_model()
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            logger.debug(
                "save_search_query: user %s not found, storing without user",
                user_id,
            )

    SearchQuery.objects.create(
        query=query,
        language_code=language_code,
        content_type=content_type,
        results_count=results_count,
        estimated_total_hits=estimated_total_hits,
        processing_time_ms=processing_time_ms,
        user=user,
        session_key=session_key,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@celery_app.task(base=MonitoredTask)
def anonymize_old_search_queries(days: int = 90) -> int:
    """Strip PII from SearchQuery rows older than ``days``.

    SearchQuery keeps ip_address / user_agent / session_key / a user FK for
    analytics, but retaining that identifiable data indefinitely is a GDPR
    liability (G0342). After the retention window we null the identifiers,
    keeping the aggregate analytics value (query text, counts, timing).
    Registered as a periodic beat task.
    """
    from django.utils import timezone
    from datetime import timedelta

    from search.models import SearchQuery

    cutoff = timezone.now() - timedelta(days=days)
    scrubbed = (
        SearchQuery.objects.filter(
            timestamp__lt=cutoff,
        )
        .exclude(
            ip_address__isnull=True,
            user_agent="",
            session_key__isnull=True,
            user__isnull=True,
        )
        .update(
            ip_address=None,
            user_agent="",
            session_key=None,
            user=None,
        )
    )
    logger.info(
        "anonymize_old_search_queries: scrubbed %s rows older than %s days",
        scrubbed,
        days,
    )
    return scrubbed
