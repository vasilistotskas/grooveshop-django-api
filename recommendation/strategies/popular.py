"""Last-resort filler: what the whole store is clicking, viewing,
liking and discounting right now.

Never declines, and scores below every real signal so it can only ever
fill the slots nothing else could. The engine additionally caps it at
one slot per response (``engine.FILLER_CAP``): a strip of four
"popular" items under a specific product says nothing about that
product.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.db.models import Count

from recommendation.enum import StrategyCode
from recommendation.strategies.base import (
    Candidate,
    RecommendationStrategy,
    register_strategy,
)
from tenant.models import TenantPlan

_TOP = 0.5
_DECAY = 0.005


@register_strategy
class PopularStrategy(RecommendationStrategy):
    code = StrategyCode.POPULAR
    label = StrategyCode.POPULAR.label
    min_plan = TenantPlan.TRIAL

    def candidates(self, product_id: int, limit: int) -> list[Candidate]:
        return self.candidates_for([product_id], limit).get(product_id, [])

    def candidates_for(
        self, seed_ids: Sequence[int], limit: int
    ) -> dict[int, list[Candidate]]:
        from product.models import Product

        # Popularity is store-wide, not per seed: ONE query, and every
        # seed receives the same list (minus the seeds themselves).
        seeds = set(seed_ids)
        ids = list(
            Product.objects.active()
            .filter(stock__gt=0)
            .exclude(id__in=seeds)
            .annotate(_likes=Count("favourited_by", distinct=True))
            .order_by(
                "-click_score",
                "-view_count",
                "-_likes",
                "-discount_percent",
                "id",
            )
            .values_list("id", flat=True)[:limit]
        )
        if not ids:
            return {}
        shared = [
            Candidate(product_id=pid, score=max(0.1, _TOP - _DECAY * rank))
            for rank, pid in enumerate(ids)
        ]
        return {seed_id: list(shared) for seed_id in seeds}
