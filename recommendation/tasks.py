"""Celery tasks: the offline half of the engine.

``recompute_candidates_for_product`` runs per product after a save
(dispatched through ``tenant.celery.dispatch_on_commit`` so the tenant
schema travels with the message). ``recompute_all_candidates`` is the
nightly full pass, reached through ``tenant.tasks.fanout_*`` because
beat runs in the public schema and this must run once per tenant.
"""

from __future__ import annotations

import logging

from core.celery import celery_app
from core.tasks import MonitoredTask

logger = logging.getLogger(__name__)

_FULL_PASS_BATCH = 200


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def recompute_candidates_for_product(product_id: int) -> dict[str, int]:
    from recommendation.candidates import compute_candidates_for_product

    rows = compute_candidates_for_product(product_id)
    return {"product_id": product_id, "rows": rows}


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def recompute_all_candidates() -> dict[str, int]:
    """Rebuild every active product's candidates in this schema.

    Iterates ids in batches rather than instances, and rebuilds one
    product per call to ``compute_candidates_for_product`` so a single
    bad row cannot abort the pass — it is logged and the pass goes on.
    """
    from product.models import Product
    from recommendation.candidates import compute_candidates_for_product
    from recommendation.context import invalidate_tenant_context

    # The nightly pass is the moment the cached tenant facts (order
    # counts, has_tags, ...) should be re-read: a strategy that was
    # declining yesterday may have earned its support overnight.
    invalidate_tenant_context()

    ids = list(
        Product.objects.active().order_by("id").values_list("id", flat=True)
    )
    rows = 0
    failed = 0
    for start in range(0, len(ids), _FULL_PASS_BATCH):
        for product_id in ids[start : start + _FULL_PASS_BATCH]:
            try:
                rows += compute_candidates_for_product(product_id)
            except Exception:
                failed += 1
                logger.exception(
                    "Candidate rebuild failed product=%s; continuing",
                    product_id,
                )
    logger.info(
        "Recommendation full pass products=%s rows=%s failed=%s",
        len(ids),
        rows,
        failed,
    )

    # One purge for the whole pass. The cache surface deliberately does
    # NOT list RecommendationCandidate in ``invalidated_by`` — see
    # core/cache/surfaces.py — because per-row signals from the
    # delete + bulk_create above would purge once per product.
    from core.cache.service import CacheService

    CacheService.purge(["recommendations"])
    return {"products": len(ids), "rows": rows, "failed": failed}


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def record_recommendation_event(
    *,
    kind: str,
    surface: str,
    impression_id: str,
    items: list[dict],
    seed_id: int | None = None,
    session_key: str = "",
    user_id: int | None = None,
) -> dict[str, int]:
    """Persist impression/click rows off the request path.

    Dispatched with ``.delay`` from a tenant request — ``TenantTask``
    stamps the schema on the message — so the rows land in the right
    schema without the caller doing anything.
    """
    from recommendation.events import record_events

    rows = record_events(
        kind=kind,
        surface=surface,
        impression_id=impression_id,
        items=items,
        seed_id=seed_id,
        session_key=session_key,
        user_id=user_id,
    )
    return {"rows": rows}
