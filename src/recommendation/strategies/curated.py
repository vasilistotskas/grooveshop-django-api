"""Merchant-curated relations — the Free tier's entire engine.

Reads ``ProductRelation`` and nothing else. Never declines: a merchant
saying "show the pot with this plant" beats any inferred similarity,
so this strategy leads every preset chain and its score is the highest
a candidate can carry.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from recommendation.enum import StrategyCode
from recommendation.strategies.base import (
    Candidate,
    RecommendationStrategy,
    register_strategy,
)
from tenant.models import TenantPlan

# The first curated relation scores 1.0; each later one a little less,
# so the merchant's own ordering survives the ranker's sort — but never
# below a floor that any inferred candidate could beat.
_DECAY = 0.02
_FLOOR = 0.7


@register_strategy
class CuratedStrategy(RecommendationStrategy):
    code = StrategyCode.CURATED
    label = StrategyCode.CURATED.label
    min_plan = TenantPlan.TRIAL

    def candidates(self, product_id: int, limit: int) -> list[Candidate]:
        return self.candidates_for([product_id], limit).get(product_id, [])

    def candidates_for(
        self, seed_ids: Sequence[int], limit: int
    ) -> dict[int, list[Candidate]]:
        from product.models import ProductRelation

        # One query for every seed; grouped in Python. Ordered by seed
        # then merchant order so the per-seed slice is the merchant's
        # top ``limit``.
        rows = (
            ProductRelation.objects.filter(
                from_product_id__in=list(seed_ids),
                to_product__active=True,
                to_product__is_deleted=False,
            )
            .order_by("from_product_id", "sort_order", "id")
            .values_list(
                "from_product_id",
                "to_product_id",
                "relation_type",
                "sort_order",
            )
        )

        out: dict[int, list[Candidate]] = defaultdict(list)
        for from_id, to_id, relation_type, sort_order in rows:
            bucket = out[from_id]
            if len(bucket) >= limit:
                continue
            bucket.append(
                Candidate(
                    product_id=to_id,
                    score=max(_FLOOR, 1.0 - _DECAY * (sort_order or 0)),
                    relation_type=relation_type,
                )
            )
        return dict(out)
