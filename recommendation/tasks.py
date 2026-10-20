"""Celery tasks: the offline half of the engine.

``recompute_candidates_for_product`` runs per product after a save
(dispatched through ``tenant.celery.dispatch_on_commit`` so the tenant
schema travels with the message). ``recompute_all_candidates`` is the
nightly full pass and ``learn_slot_weights`` the nightly weight update;
both are reached through ``tenant.tasks.fanout_*`` because beat runs in
the public schema and they must run once per tenant.

The candidate tasks write rows for the strategies whose ``precompute``
is True — ``attributes`` and ``co_purchase`` — and nothing for the live
ones, which the engine answers per request.
"""

from __future__ import annotations

import logging

from core.celery import celery_app
from core.retry import TRANSIENT_ERRORS
from core.tasks import MonitoredTask

logger = logging.getLogger(__name__)

_FULL_PASS_BATCH = 200


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
)
def recompute_candidates_for_product(product_id: int) -> dict[str, int]:
    from recommendation.candidates import compute_candidates
    from recommendation.context import recommendations_active

    if not recommendations_active():
        return {"product_id": product_id, "rows": 0}
    rows = compute_candidates([product_id])
    return {"product_id": product_id, "rows": rows}


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
)
def recompute_all_candidates() -> dict[str, int]:
    """Rebuild every active product's candidates in this schema.

    Batches of ``_FULL_PASS_BATCH`` seeds, each answered by every
    precomputed strategy in a fixed number of queries; a batch that
    fails is logged and the pass goes on. A store with suggestions off
    computes nothing: no strip will read the rows.
    """
    from product.models import Product
    from recommendation.candidates import compute_candidates
    from recommendation.context import (
        invalidate_tenant_context,
        recommendations_active,
    )

    if not recommendations_active():
        return {"products": 0, "rows": 0, "failed": 0}

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
        batch = ids[start : start + _FULL_PASS_BATCH]
        try:
            rows += compute_candidates(batch)
        except Exception:
            failed += len(batch)
            logger.exception(
                "Candidate rebuild failed products=%s..%s; continuing",
                batch[0],
                batch[-1],
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
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
)
def learn_slot_weights() -> dict[str, int]:
    """Re-weight each learning slot's strategies from attach rate.

    See ``recommendation.learning``. A store with suggestions off shows
    no strips, so it has no new evidence to learn from.
    """
    from recommendation.context import recommendations_active
    from recommendation.learning import learn_weights

    if not recommendations_active():
        return {"slots": 0}
    return {"slots": learn_weights()}


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
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
    cart_uuid: str | None = None,
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
        cart_uuid=cart_uuid,
    )
    return {"rows": rows}


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
)
def record_recommendation_attach(order_id: int) -> dict[str, int]:
    """Write ``attach`` rows for a newly created order.

    Dispatched from ``recommendation.signals.on_order_created`` through
    ``dispatch_on_commit`` so the schema travels with the message. Safe
    to retry: the attach rows are unique per (order, product,
    impression).
    """
    from recommendation.events import record_attach_events

    rows = record_attach_events(order_id)
    return {"order_id": order_id, "rows": rows}
