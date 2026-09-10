"""The online half of two-stage retrieval: merge, guard, weight, cut.

``suggest`` reads the slot for a surface, collects candidates for the
seed products — live from the cheap strategies, from the precomputed
table for the expensive ones — applies the guardrails every tenant
gets, blends scores with the slot's per-strategy weights, diversifies,
and cuts to the slot's limit. Below ``min_fill`` it returns nothing,
because one lonely suggestion looks broken.

It returns product IDs plus a ``reason`` per item, never serialized
products: hydration happens in the view with ``Product.objects
.for_list()`` so pricing context (B2B customer group) and locale are
applied per request while everything here stays cacheable per product.

Query cost is bounded by the number of strategies in the chain, never
by the number of seeds: every live strategy answers the whole seed set
through ``candidates_for`` in a fixed number of queries, and the table
read for precomputed ones is a single ``IN`` query.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import NamedTuple

from recommendation.candidates import compute_candidates_for_product
from recommendation.context import tenant_context
from recommendation.enum import StrategyCode, Surface
from recommendation.models import RecommendationCandidate, RecommendationSlot
from recommendation.presets import slot_defaults
from recommendation.strategies import RecommendationStrategy, get_strategy

logger = logging.getLogger(__name__)

# A product nominated for more than one seed (two cart lines both
# point at it) is the thing that turns "more of the same" into
# "complete the order". Added per extra seed hit.
BASKET_BONUS = 0.25

# ``popular`` is filler. It may never crowd out a real signal.
FILLER_CAP = 1


@dataclass(frozen=True, slots=True)
class Suggestion:
    product_id: int
    strategy: str
    relation_type: str | None
    score: float


@dataclass(frozen=True, slots=True)
class SuggestionContext:
    surface: str
    seed_ids: tuple[int, ...]
    exclude_ids: tuple[int, ...] = ()
    limit: int | None = None


class _Row(NamedTuple):
    """One candidate for one seed, whichever path produced it."""

    seed_id: int
    candidate_id: int
    strategy: str
    score: float
    relation_type: str | None


@dataclass(slots=True)
class _Merged:
    score: float = 0.0
    seeds_hit: set[int] = field(default_factory=set)
    strategy: str = ""
    strategy_score: float = 0.0
    relation_type: str | None = None
    # Merchant intent is a TIER, not a score. Blending is additive, so
    # a candidate three inferred strategies agree on can out-sum a
    # curated one — measured on staging: variant_group + category +
    # popular came to 2.2 against a curated 1.0 + 0.5. A merchant
    # saying "show the pot with this plant" must not lose to that, so
    # anything with a curated contribution sorts before anything
    # without one, and the blended score orders within each tier.
    curated: bool = False


def _slot(surface: str) -> RecommendationSlot:
    slot = RecommendationSlot.objects.filter(surface=surface).first()
    if slot is not None:
        return slot
    # No row yet — a tenant provisioned before the engine existed and
    # not yet backfilled. The preset is the behaviour it would have
    # been seeded with; this is never persisted from here.
    defaults = slot_defaults(surface)
    return RecommendationSlot(surface=surface, **defaults)


def _available_chain(
    slot: RecommendationSlot,
) -> list[RecommendationStrategy]:
    ctx = tenant_context()
    chain: list[RecommendationStrategy] = []
    for code in slot.strategy_chain:
        try:
            strategy = get_strategy(code)
        except KeyError:
            # A preset may name a strategy from a later step (semantic,
            # co_purchase) that this build does not register yet. That
            # is expected until it ships, so debug rather than warning.
            logger.debug("Strategy %r not registered; skipping", code)
            continue
        if strategy.is_available(ctx):
            chain.append(strategy)
    return chain


def _stored_rows(
    seed_ids: tuple[int, ...], strategies: list[str]
) -> dict[int, list[_Row]]:
    by_seed: dict[int, list[_Row]] = defaultdict(list)
    rows = RecommendationCandidate.objects.filter(
        product_id__in=seed_ids, strategy__in=strategies
    ).values_list(
        "product_id", "candidate_id", "strategy", "score", "relation_type"
    )
    for seed_id, candidate_id, strategy, score, relation_type in rows:
        by_seed[seed_id].append(
            _Row(
                seed_id,
                candidate_id,
                strategy,
                float(score),
                relation_type or None,
            )
        )
    return dict(by_seed)


def _collect(
    seed_ids: tuple[int, ...],
    chain: list[RecommendationStrategy],
    per_strategy_limit: int,
) -> list[_Row]:
    rows: list[_Row] = []

    # Live strategies: the whole seed set per strategy, bounded queries.
    for strategy in chain:
        if strategy.precompute:
            continue
        for seed_id, candidates in strategy.candidates_for(
            seed_ids, per_strategy_limit
        ).items():
            rows.extend(
                _Row(
                    seed_id,
                    candidate.product_id,
                    strategy.code,
                    float(candidate.score),
                    candidate.relation_type,
                )
                for candidate in candidates
            )

    # Precomputed strategies: one table read; a seed with no rows at
    # all has never been computed (first request after creation, or
    # before the nightly pass) — compute it now, bounded by K per
    # strategy, and read it back.
    stored = [s.code for s in chain if s.precompute]
    if stored:
        found = _stored_rows(seed_ids, stored)
        for seed_id in seed_ids:
            if seed_id not in found:
                compute_candidates_for_product(seed_id)
                found.update(_stored_rows((seed_id,), stored))
        for seed_rows in found.values():
            rows.extend(seed_rows)
    return rows


def _final_price(price: Decimal, discount_percent: Decimal) -> Decimal:
    return price - (price * discount_percent / Decimal(100))


def suggest(ctx: SuggestionContext) -> list[Suggestion]:
    if not ctx.seed_ids:
        return []

    slot = _slot(ctx.surface)
    if not slot.enabled:
        return []
    limit = ctx.limit or slot.limit

    chain = _available_chain(slot)
    if not chain:
        return []

    # Ask each strategy for more than the slot shows, so the guard and
    # the diversity caps have something to drop.
    rows = _collect(ctx.seed_ids, chain, per_strategy_limit=limit * 3)
    weights = {s.code: float(slot.weights.get(s.code, 1.0)) for s in chain}
    excluded = set(ctx.seed_ids) | set(ctx.exclude_ids)

    merged: dict[int, _Merged] = defaultdict(_Merged)
    for row in rows:
        if row.candidate_id in excluded:
            continue
        weighted = weights[row.strategy] * row.score
        entry = merged[row.candidate_id]
        entry.score += weighted
        entry.seeds_hit.add(row.seed_id)
        if row.strategy == StrategyCode.CURATED:
            entry.curated = True
        # The reason shown is the strongest single contribution.
        if weighted > entry.strategy_score:
            entry.strategy_score = weighted
            entry.strategy = row.strategy
            entry.relation_type = row.relation_type
    if not merged:
        return []

    for entry in merged.values():
        entry.score += BASKET_BONUS * (len(entry.seeds_hit) - 1)

    # ---- guard: live facts about the candidates and the seeds -------
    from product.models import Product

    ids = list(merged) + list(ctx.seed_ids)
    facts = {
        row["id"]: row
        for row in Product.objects.active()
        .filter(id__in=ids)
        .values("id", "stock", "category_id", "price", "discount_percent")
    }
    band = slot.price_band_ratio
    seed_prices = [
        _final_price(facts[s]["price"], facts[s]["discount_percent"])
        for s in ctx.seed_ids
        if s in facts
    ]

    def passes_guard(product_id: int) -> bool:
        fact = facts.get(product_id)
        if fact is None or fact["stock"] <= 0:
            return False
        if band and seed_prices:
            price = _final_price(fact["price"], fact["discount_percent"])
            ratio = Decimal(band)
            return any(
                seed / ratio <= price <= seed * ratio for seed in seed_prices
            )
        return True

    ranked = sorted(
        (pid for pid in merged if passes_guard(pid)),
        key=lambda pid: (not merged[pid].curated, -merged[pid].score, pid),
    )

    # ---- diversify: filler capped, no single category dominating ----
    category_cap = max(1, limit - 1)
    per_category: dict[int | None, int] = defaultdict(int)
    filler_used = 0
    chosen: list[Suggestion] = []
    for pid in ranked:
        entry = merged[pid]
        if entry.strategy == StrategyCode.POPULAR and filler_used >= FILLER_CAP:
            continue
        category_id = facts[pid]["category_id"]
        if (
            ctx.surface != Surface.PDP
            and per_category[category_id] >= category_cap
        ):
            continue
        chosen.append(
            Suggestion(
                product_id=pid,
                strategy=entry.strategy,
                relation_type=entry.relation_type,
                score=round(entry.score, 4),
            )
        )
        per_category[category_id] += 1
        if entry.strategy == StrategyCode.POPULAR:
            filler_used += 1
        if len(chosen) >= limit:
            break

    if len(chosen) < slot.min_fill:
        return []
    return chosen
