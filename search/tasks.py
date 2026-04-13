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
