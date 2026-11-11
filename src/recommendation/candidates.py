"""The offline half of two-stage retrieval.

``compute_candidates`` asks every available precomputed strategy for its
top-K candidates for a batch of seeds and replaces those seeds' rows in
``RecommendationCandidate`` atomically. Celery calls it for one product
on save (``tasks.recompute_candidates_for_product``) and for the whole
catalogue in batches nightly (``tasks.recompute_all_candidates``). The
request path never calls it: a precomputed strategy is precomputed
because it is too expensive for a request, and a seed with no rows is
far more often "this product has no co-purchases" than "not computed
yet" — so computing on a miss would run the expensive path on every
view of every such product. A new product is computed by its own
``post_save`` within seconds instead.

K is fixed and small on purpose: the request path merges these rows in
memory, so their count is the request path's cost.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal

from django.db import transaction

from recommendation.context import tenant_context
from recommendation.models import RecommendationCandidate
from recommendation.strategies import iter_strategies

logger = logging.getLogger(__name__)

CANDIDATES_PER_STRATEGY = 50


def compute_candidates(product_ids: Sequence[int]) -> int:
    """Rebuild the candidate rows for ``product_ids``; return how many.

    Each strategy answers the whole batch through ``candidates_for``, so
    a batch costs a fixed number of queries per strategy rather than a
    number per product.
    """
    seeds = list(product_ids)
    if not seeds:
        return 0
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
        answers = strategy.candidates_for(seeds, CANDIDATES_PER_STRATEGY)
        for seed_id, candidates in answers.items():
            rows.extend(
                RecommendationCandidate(
                    product_id=seed_id,
                    candidate_id=candidate.product_id,
                    strategy=strategy.code,
                    score=Decimal(str(round(candidate.score, 4))),
                    relation_type=candidate.relation_type or "",
                )
                for candidate in candidates
                if candidate.product_id != seed_id
            )

    # Also when nothing was computed: a strategy that stopped being
    # available (the plan changed, the store's data no longer supports
    # it) must not keep serving the rows it wrote before.
    with transaction.atomic():
        RecommendationCandidate.objects.filter(product_id__in=seeds).delete()
        # ``ignore_conflicts``: two strategies may legitimately nominate
        # the same product; the unique constraint is on (product,
        # candidate, strategy), so that is allowed — this only guards a
        # strategy returning the same id twice.
        RecommendationCandidate.objects.bulk_create(rows, ignore_conflicts=True)

    logger.info(
        "Recommendation candidates rebuilt products=%s rows=%s skipped=%s",
        len(seeds),
        len(rows),
        skipped,
    )
    return len(rows)
