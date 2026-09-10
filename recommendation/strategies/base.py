"""The strategy contract and its registry.

Mirrors ``shipping/interfaces.py``: a small ABC, a class decorator
that instantiates and registers, a module-level ``_REGISTRY`` populated
when the package is imported from ``AppConfig.ready()``.

The one idea that is not borrowed from the carriers is
``is_available``: a strategy is asked whether it can answer WELL for
this tenant before it is asked to answer at all. Co-purchase declines
below a support threshold, ``attributes`` declines when the tenant has
populated none of the signals it reads, ``semantic`` declines when the
index has no embedder. **A strategy that cannot answer well returns
nothing rather than noise** — that is what makes the engine safe for a
store with zero orders, which is every store on its first day.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

from tenant.models import TenantPlan

# Rank, not equality: ``min_plan`` means "this plan or any above it".
_PLAN_RANK: dict[str, int] = {
    TenantPlan.TRIAL: 0,
    TenantPlan.BASIC: 1,
    TenantPlan.PRO: 2,
    TenantPlan.ENTERPRISE: 3,
}


def plan_allows(plan: str, min_plan: str) -> bool:
    """True when ``plan`` is at or above ``min_plan``.

    An unknown plan ranks as the Free tier, so a typo in a Tenant row
    degrades to the Free capability set rather than unlocking
    everything — or blanking every strip on the store.
    """
    free = _PLAN_RANK[TenantPlan.TRIAL]
    return _PLAN_RANK.get(plan, free) >= _PLAN_RANK.get(min_plan, free)


class Candidate(NamedTuple):
    """One suggestion from one strategy, before ranking.

    ``score`` is the strategy's own confidence in ``0..1``; the ranker
    multiplies it by the slot's per-strategy weight, so strategies
    never need to agree on a scale between themselves.
    ``relation_type`` is set only by ``curated`` and is what the
    storefront turns into a label ("Goes well with").
    """

    product_id: int
    score: float
    relation_type: str | None = None


@dataclass(frozen=True, slots=True)
class TenantContext:
    """What a strategy is allowed to know about the tenant when deciding
    whether it can answer.

    Built once per request by ``recommendation.context.tenant_context``
    from cheap ``EXISTS``/``COUNT`` queries and cached for five minutes
    under the schema-scoped default cache. Strategies read it; they
    never query these facts themselves.
    """

    plan: str
    product_count: int
    has_brands: bool
    has_tags: bool
    has_attributes: bool
    order_count: int
    multi_item_order_count: int
    semantic_available: bool


class RecommendationStrategy(ABC):
    """One way of producing candidates for a seed product.

    Subclasses set ``code`` (a ``StrategyCode`` value — the enum is what
    slot configuration is validated against), ``label``, and
    ``min_plan``. They implement ``candidates``; they override
    ``is_available`` when they have a reason to decline beyond the plan
    gate, and MUST call ``super().is_available(ctx)`` first.
    """

    code: str = ""
    label: str = ""
    min_plan: str = TenantPlan.TRIAL
    # Whether Celery precomputes this strategy's candidates into
    # ``RecommendationCandidate`` (True) or the engine calls
    # ``candidates()`` live on every request (False).
    #
    # Live is the right default for anything that is one cheap ORM
    # query: it is always fresh — a product created a minute ago shows
    # up beside its category siblings immediately, instead of after the
    # nightly pass — and it costs no more than the table read would.
    # Precompute is for strategies whose answer is expensive to derive
    # (a Meilisearch vector query, a co-occurrence aggregation over
    # every order), where the offline table is what keeps the request
    # path constant-cost at any catalogue size.
    precompute: bool = False

    def is_available(self, ctx: TenantContext) -> bool:
        return plan_allows(ctx.plan, self.min_plan)

    @abstractmethod
    def candidates(self, product_id: int, limit: int) -> list[Candidate]:
        """Up to ``limit`` candidates for ``product_id``, best first.

        Runs in the tenant's schema context. Must never raise for an
        unknown or inactive product — return ``[]``; the engine treats
        an empty list as "this strategy has nothing", which is a valid
        answer, not an error.
        """

    def candidates_for(
        self, seed_ids: Sequence[int], limit: int
    ) -> dict[int, list[Candidate]]:
        """Candidates for MANY seeds — the entry point the engine uses.

        The cart surface seeds with every line in the basket, so a
        strategy answered once per seed costs a basket of N lines N
        times its queries: measured, cart detail went from 30 to 42
        queries as items were added. Live strategies therefore override
        this to answer the whole seed set in a bounded number of
        queries, whatever N is. The default loops ``candidates`` and is
        only acceptable for a strategy whose per-seed cost is zero
        queries (a precomputed one, read from the table by the engine).
        """
        return {
            seed_id: self.candidates(seed_id, limit) for seed_id in seed_ids
        }


# Module-level registry. Populated by @register_strategy when
# ``recommendation.strategies`` is imported from AppConfig.ready().
_REGISTRY: dict[str, RecommendationStrategy] = {}


def register_strategy(
    cls: type[RecommendationStrategy],
) -> type[RecommendationStrategy]:
    """Class decorator that instantiates and registers a strategy.

    Re-registering the same code overwrites the previous instance — on
    purpose, so a test can swap in a stub without touching the registry
    internals.
    """
    # ``str()``: the class attribute is usually a ``StrategyCode``
    # member, and a plain-string key keeps the registry's keys — and
    # the "known:" list in the error below — free of enum reprs.
    code = str(cls.code)
    if not code:
        raise ValueError(
            f"{cls.__name__} must declare a non-empty class-level 'code' "
            "before @register_strategy."
        )
    _REGISTRY[code] = cls()
    return cls


def get_strategy(code: str) -> RecommendationStrategy:
    """Return the strategy registered under ``code`` or raise."""
    try:
        return _REGISTRY[code]
    except KeyError as exc:
        raise KeyError(
            f"No recommendation strategy registered as {code!r}; "
            f"known: {sorted(_REGISTRY)}"
        ) from exc


def iter_strategies() -> list[RecommendationStrategy]:
    """Every registered strategy, in registration order."""
    return list(_REGISTRY.values())
