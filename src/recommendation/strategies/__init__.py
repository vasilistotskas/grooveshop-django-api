"""Strategy registry.

Importing this package registers every concrete strategy — each module
below applies ``@register_strategy`` at import time, the same way the
shipping carriers register in ``AppConfig.ready()``. Keep the import
list explicit: a strategy that is not imported here does not exist as
far as ``get_strategy`` is concerned, however correct its module is —
and ``tests/unit/recommendation/test_registry.py`` fails for any
``StrategyCode`` member with no registered strategy.
"""

# Concrete strategies — importing registers them. Order here is
# registration order, which ``iter_strategies`` preserves; the ORDER
# THAT MATTERS is the slot's ``strategy_chain``, not this list.
from recommendation.strategies import (
    attributes,
    category,
    co_purchase,
    curated,
    popular,
    variant_group,
)
from recommendation.strategies.base import (
    Candidate,
    RecommendationStrategy,
    TenantContext,
    get_strategy,
    iter_strategies,
    register_strategy,
)

__all__ = [
    "Candidate",
    "RecommendationStrategy",
    "TenantContext",
    "attributes",
    "category",
    "co_purchase",
    "curated",
    "get_strategy",
    "iter_strategies",
    "popular",
    "register_strategy",
    "variant_group",
]
