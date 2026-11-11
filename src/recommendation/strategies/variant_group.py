"""Siblings in the same ``ProductVariantGroup`` — the same item in
another colour or size. Universal signal, no plan gate; declines by
returning nothing for a product that has no group."""

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

_SCORE = 0.9


@register_strategy
class VariantGroupStrategy(RecommendationStrategy):
    code = StrategyCode.VARIANT_GROUP
    label = StrategyCode.VARIANT_GROUP.label
    min_plan = TenantPlan.TRIAL

    def candidates(self, product_id: int, limit: int) -> list[Candidate]:
        return self.candidates_for([product_id], limit).get(product_id, [])

    def candidates_for(
        self, seed_ids: Sequence[int], limit: int
    ) -> dict[int, list[Candidate]]:
        from product.models import Product

        # Two queries whatever the seed count: which group each seed is
        # in, then every active member of those groups.
        group_of = dict(
            Product.objects.filter(
                id__in=list(seed_ids), variant_group_id__isnull=False
            ).values_list("id", "variant_group_id")
        )
        if not group_of:
            return {}

        members: dict[int, list[int]] = defaultdict(list)
        rows = (
            Product.objects.active()
            .filter(variant_group_id__in=set(group_of.values()))
            .order_by("-stock", "id")
            .values_list("variant_group_id", "id")
        )
        for group_id, product_id in rows:
            members[group_id].append(product_id)

        out: dict[int, list[Candidate]] = {}
        for seed_id, group_id in group_of.items():
            siblings = [
                pid for pid in members.get(group_id, []) if pid != seed_id
            ]
            if siblings:
                out[seed_id] = [
                    Candidate(product_id=pid, score=_SCORE)
                    for pid in siblings[:limit]
                ]
        return out
