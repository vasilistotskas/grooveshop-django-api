"""The offline half of two-stage retrieval.

``compute_candidates_for_product`` asks every available strategy for
its top-K candidates for one seed and replaces that seed's rows in
``RecommendationCandidate`` atomically. Celery calls it per product on
save and in bulk nightly; the online engine calls it synchronously as
a cold-path fallback when a seed has no rows yet, which is what a
brand-new catalogue looks like on its first request.

K is fixed and small on purpose: the request path merges these rows in
memory, so their count is the request path's cost.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction

from recommendation.context import tenant_context
from recommendation.models import RecommendationCandidate
from recommendation.strategies import iter_strategies

logger = logging.getLogger(__name__)

CANDIDATES_PER_STRATEGY = 50


def compute_candidates_for_product(product_id: int) -> int:
    """Rebuild the candidate rows for ``product_id``; return how many."""
    ctx = tenant_context()
    rows: list[RecommendationCandidate] = []
    skipped: list[str] = []

    for strategy in iter_strategies():
        # Live strategies are answered at request time by the engine;
        # writing their rows here would only make them stale.
        if not strategy.precompute:
            continue
        if not strategy.is_available(ctx):
            skipped.append(strategy.code)
            continue
        for candidate in strategy.candidates(
            product_id, CANDIDATES_PER_STRATEGY
        ):
            if candidate.product_id == product_id:
                continue
            rows.append(
                RecommendationCandidate(
                    product_id=product_id,
                    candidate_id=candidate.product_id,
                    strategy=strategy.code,
                    score=Decimal(str(round(candidate.score, 4))),
                    relation_type=candidate.relation_type or "",
                )
            )

    with transaction.atomic():
        RecommendationCandidate.objects.filter(product_id=product_id).delete()
        # ``ignore_conflicts``: two strategies may legitimately nominate
        # the same product (a curated relation that is also in the same
        # category); the unique constraint is on (product, candidate,
        # strategy), so that is allowed — this only guards a strategy
        # returning the same id twice.
        RecommendationCandidate.objects.bulk_create(rows, ignore_conflicts=True)

    logger.info(
        "Recommendation candidates rebuilt product=%s rows=%s skipped=%s",
        product_id,
        len(rows),
        skipped,
    )
    return len(rows)
