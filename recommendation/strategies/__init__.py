"""Strategy registry.

Importing this package registers every concrete strategy — each module
below applies ``@register_strategy`` at import time, the same way the
shipping carriers register in ``AppConfig.ready()``. Keep the import
list explicit: a strategy that is not imported here does not exist as
far as ``get_strategy`` is concerned, however correct its module is.
"""

# Concrete strategies — importing registers them. Order here is
# registration order, which ``iter_strategies`` preserves; the ORDER
# THAT MATTERS is the slot's ``strategy_chain``, not this list.
from recommendation.strategies import (
    category,
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
    "category",
    "curated",
    "get_strategy",
    "iter_strategies",
    "popular",
    "register_strategy",
    "variant_group",
]
