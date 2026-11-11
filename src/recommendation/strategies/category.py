"""Same category first, then sibling categories, then the parent's
whole subtree — scored by tier, ordered within a tier by demonstrated
interest (``click_score``, ``view_count``), so this is "what else sells
here", not "what was created most recently".

Exactly three queries, whatever the seed count and however full the
categories are:

1. the seeds' categories, with their parents' MPTT bounds;
2. every active category inside those parents' subtrees (one range
   query — siblings, the seed's own category and deeper descendants
   all fall inside a parent's ``lft``/``rght``);
3. every active product in those categories, best first.

Tiers are then assigned in Python. A widen-as-needed walk was tried
first and made the query count depend on how many tiers a seed
reached — this suite's query-budget tests demand cost that does not
vary with data, and they are right to.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from django.db.models import Q

from recommendation.enum import StrategyCode
from recommendation.strategies.base import (
    Candidate,
    RecommendationStrategy,
    register_strategy,
)
from tenant.models import TenantPlan

_SAME = 0.8
_SIBLING = 0.6
_SUBTREE = 0.5
_ORDERING = ("-click_score", "-view_count", "id")


@register_strategy
class CategoryStrategy(RecommendationStrategy):
    code = StrategyCode.CATEGORY
    label = StrategyCode.CATEGORY.label
    min_plan = TenantPlan.TRIAL

    def candidates(self, product_id: int, limit: int) -> list[Candidate]:
        return self.candidates_for([product_id], limit).get(product_id, [])

    def candidates_for(
        self, seed_ids: Sequence[int], limit: int
    ) -> dict[int, list[Candidate]]:
        from product.models import Product, ProductCategory

        seeds = list(seed_ids)

        # Query 1: each seed's category and the bounds of the subtree
        # we will draw from — the parent's when there is one (so that
        # siblings are included), the category's own otherwise.
        seed_rows = Product.objects.filter(
            id__in=seeds, category_id__isnull=False
        ).values_list(
            "id",
            "category_id",
            "category__parent_id",
            "category__tree_id",
            "category__lft",
            "category__rght",
            "category__parent__tree_id",
            "category__parent__lft",
            "category__parent__rght",
        )
        seed_category: dict[int, int] = {}
        seed_parent: dict[int, int | None] = {}
        ranges: dict[int, tuple[int, int, int]] = {}
        for pid, cat, parent, tree, lft, rght, ptree, plft, prght in seed_rows:
            seed_category[pid] = cat
            seed_parent[pid] = parent
            ranges[pid] = (ptree, plft, prght) if parent else (tree, lft, rght)
        if not seed_category:
            return {}

        # Query 2: every active category inside any of those subtrees.
        span = Q()
        for tree, lft, rght in set(ranges.values()):
            span |= Q(tree_id=tree, lft__gte=lft, rght__lte=rght)
        category_parent: dict[int, int | None] = dict(
            ProductCategory.objects.filter(span, active=True).values_list(
                "id", "parent_id"
            )
        )

        # Query 3: every active product in those categories, best first
        # within each category.
        products_by_category: dict[int, list[int]] = defaultdict(list)
        for cat, pid in (
            Product.objects.active()
            .filter(category_id__in=category_parent)
            .order_by("category_id", *_ORDERING)
            .values_list("category_id", "id")
        ):
            products_by_category[cat].append(pid)

        seed_set = set(seeds)
        out: dict[int, list[Candidate]] = {}
        for seed_id, own in seed_category.items():
            parent = seed_parent[seed_id]
            tiers = (
                (_SAME, [own]),
                (
                    _SIBLING,
                    [
                        cat
                        for cat, cat_parent in category_parent.items()
                        if cat != own and cat_parent == parent and parent
                    ],
                ),
                (
                    _SUBTREE,
                    [
                        cat
                        for cat, cat_parent in category_parent.items()
                        if cat != own and (cat_parent != parent or not parent)
                    ],
                ),
            )
            bucket: list[Candidate] = []
            seen = set(seed_set)
            for score, categories in tiers:
                for cat in categories:
                    for pid in products_by_category.get(cat, ()):
                        if len(bucket) >= limit:
                            break
                        if pid in seen:
                            continue
                        seen.add(pid)
                        bucket.append(Candidate(product_id=pid, score=score))
                if len(bucket) >= limit:
                    break
            if bucket:
                out[seed_id] = bucket
        return out
