import logging

from core import celery_app
from core.tasks import MonitoredTask

logger = logging.getLogger(__name__)

# Click-through ranking signal (Phase 1 of the search plan). Values are
# the guardrails Findloom converged on after shipping the uncapped
# version: an unbounded list burned ranking work on single-click
# long-tail noise, so only items with a minimum of repeat clicks inside
# a rolling window feed the signal, capped to a small head.
CLICK_SCORE_WINDOW_DAYS = 30
CLICK_SCORE_MIN_CLICKS = 2
CLICK_SCORE_MAX_ITEMS = 50


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
    query_uuid: str | None = None,
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
        uuid=query_uuid,
        # CharField(max_length=500) is only enforced by the database on
        # .create(); an unbounded pasted query must not kill the task.
        query=query[:500],
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


class _SearchQueryNotSavedYet(Exception):
    """The SearchQuery row for a click has not been persisted yet.

    ``save_search_query`` runs asynchronously, so a click arriving
    milliseconds after the search can reference a ``query_id`` whose row
    is still in flight. Retrying with backoff absorbs that race.
    """


@celery_app.task(
    base=MonitoredTask,
    max_retries=5,
    autoretry_for=(_SearchQueryNotSavedYet,),
    retry_backoff=True,
)
def save_search_click(
    query_uuid: str,
    result_id: str,
    result_type: str,
    position: int,
) -> None:
    """Persist a SearchClick attributed to a SearchQuery by its uuid."""
    from search.models import SearchClick, SearchQuery

    try:
        search_query = SearchQuery.objects.get(uuid=query_uuid)
    except SearchQuery.DoesNotExist:
        raise _SearchQueryNotSavedYet(query_uuid) from None

    SearchClick.objects.create(
        search_query=search_query,
        result_id=result_id,
        result_type=result_type,
        position=position,
    )


@celery_app.task(base=MonitoredTask)
def update_click_scores() -> dict[str, int]:
    """Refresh the ``click_score`` ranking signal from recent clicks.

    Counts SearchClick rows per result over the trailing window, keeps
    only results with at least CLICK_SCORE_MIN_CLICKS (single clicks are
    noise, not preference), caps the list at CLICK_SCORE_MAX_ITEMS per
    type, writes the counts to the ``click_score`` model fields, and
    pushes partial document updates to Meilisearch so ranking reflects
    them without a full resync. Results that fell out of the window are
    reset to zero. Safe no-op while no clicks exist yet.
    """
    from datetime import timedelta

    from django.db.models import Count
    from django.utils import timezone

    from blog.models.post import BlogPost, BlogPostTranslation
    from product.models.product import Product, ProductTranslation
    from search.models import SearchClick

    cutoff = timezone.now() - timedelta(days=CLICK_SCORE_WINDOW_DAYS)
    counted: dict[str, dict[int, int]] = {"product": {}, "blog_post": {}}
    rows = (
        SearchClick.objects.filter(timestamp__gte=cutoff)
        .values("result_type", "result_id")
        .annotate(clicks=Count("id"))
        .filter(clicks__gte=CLICK_SCORE_MIN_CLICKS)
    )
    for row in rows:
        bucket = counted.get(row["result_type"])
        if bucket is None:
            continue
        try:
            bucket[int(row["result_id"])] = row["clicks"]
        except TypeError, ValueError:
            logger.warning(
                "update_click_scores: non-numeric result_id %r skipped",
                row["result_id"],
            )

    changed = {}
    for result_type, model, translation_model in (
        ("product", Product, ProductTranslation),
        ("blog_post", BlogPost, BlogPostTranslation),
    ):
        scores = dict(
            sorted(
                counted[result_type].items(),
                key=lambda item: item[1],
                reverse=True,
            )[:CLICK_SCORE_MAX_ITEMS]
        )
        changed[result_type] = _apply_click_scores(
            model, translation_model, scores
        )

    logger.info(
        "update_click_scores: %s products, %s blog posts updated",
        changed["product"],
        changed["blog_post"],
    )
    return changed


def _apply_click_scores(
    model, translation_model, scores: dict[int, int]
) -> int:
    """Write ``scores`` to the model and mirror them into Meilisearch.

    Every master not in ``scores`` is reset to zero. Returns the number
    of masters whose score actually changed.
    """

    changed_ids: list[int] = []

    stale = model.objects.exclude(click_score=0)
    if scores:
        stale = stale.exclude(pk__in=scores.keys())
    changed_ids.extend(stale.values_list("pk", flat=True))
    stale.update(click_score=0)

    for pk, clicks in scores.items():
        updated = (
            model.objects.filter(pk=pk)
            .exclude(click_score=clicks)
            .update(click_score=clicks)
        )
        if updated:
            changed_ids.append(pk)

    if changed_ids:
        _push_click_scores_to_meili(model, translation_model, changed_ids)
    return len(changed_ids)


def _push_click_scores_to_meili(
    model, translation_model, master_ids: list[int]
) -> None:
    """Partial-update the ``click_score`` attribute on affected documents.

    One document exists per translation, so every translation of a
    changed master is updated. Skipped entirely in OFFLINE mode (tests).
    """
    from django.conf import settings

    if settings.MEILISEARCH.get("OFFLINE", False):
        return

    from meili._client import client as meili_client

    score_by_master = dict(
        model.objects.filter(pk__in=master_ids).values_list("pk", "click_score")
    )
    documents = [
        {"id": translation_pk, "click_score": score_by_master[master_pk]}
        for translation_pk, master_pk in model.objects.filter(pk__in=master_ids)
        .values_list("translations__pk", "pk")
        .exclude(translations__pk__isnull=True)
    ]
    if documents:
        # Resolved at push time so the {schema}__ prefix reflects the
        # tenant schema the task is running in (fanout enters each
        # tenant's schema_context before dispatching).
        index_name = translation_model.get_meili_index_name()
        meili_client.get_index(index_name).update_documents(documents)


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
