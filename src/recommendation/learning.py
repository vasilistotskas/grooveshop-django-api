"""Learn each slot's strategy weights from what shoppers bought.

The signal is attach rate: of the products a strategy put in front of
shoppers on a surface (``impression`` rows, one per product shown), the
share that ended up in an order (``attach`` rows, written by
``events.record_attach_events``). Over the trailing
``RECOMMENDATION_LEARNING_WINDOW_DAYS``, per surface and strategy:

* a strategy with fewer than ``RECOMMENDATION_LEARNING_MIN_IMPRESSIONS``
  impressions keeps the weight it has — too few showings to tell a good
  strategy from a lucky one;
* the strategy with the best attach rate weighs 1.0 and every other
  measured one ``its rate / the best rate``, never below
  ``RECOMMENDATION_LEARNING_WEIGHT_FLOOR`` — a strategy weighted to zero
  is never shown again, so it could never earn its way back;
* when no measured strategy attached anything, there is no evidence of
  a difference and nothing changes.

Only slots with ``learn_weights`` on are touched; a merchant who sets
weights by hand turns it off. A changed slot is saved through the ORM,
so the ``recommendations`` cache surface purges the cached strips.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from recommendation.enum import EventKind
from recommendation.models import RecommendationEvent, RecommendationSlot

logger = logging.getLogger(__name__)


def learned_weights(
    chain: list[str],
    current: dict[str, float],
    impressions: dict[str, int],
    attaches: dict[str, int],
) -> dict[str, float]:
    """The new weights for one slot, from its per-strategy counts.

    Pure, so the rule can be tested without events. Returns ``current``
    unchanged when there is nothing to learn from.
    """
    minimum = settings.RECOMMENDATION_LEARNING_MIN_IMPRESSIONS
    rates = {
        code: attaches.get(code, 0) / impressions[code]
        for code in chain
        if impressions.get(code, 0) >= minimum
    }
    best = max(rates.values(), default=0.0)
    if best <= 0:
        return dict(current)
    floor = settings.RECOMMENDATION_LEARNING_WEIGHT_FLOOR
    weights = {code: w for code, w in current.items() if code in chain}
    for code, rate in rates.items():
        weights[code] = round(max(floor, rate / best), 4)
    return weights


def learn_weights() -> int:
    """Update every learning slot in this schema; return how many changed."""
    since = timezone.now() - timedelta(
        days=settings.RECOMMENDATION_LEARNING_WINDOW_DAYS
    )
    counts: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for surface, strategy, kind, n in (
        RecommendationEvent.objects.filter(
            created_at__gte=since,
            kind__in=[EventKind.IMPRESSION, EventKind.ATTACH],
        )
        .values("surface", "strategy", "kind")
        .annotate(n=Count("id"))
        .values_list("surface", "strategy", "kind", "n")
    ):
        counts[(surface, kind)][strategy] = n

    changed = 0
    for slot in RecommendationSlot.objects.filter(
        learn_weights=True, enabled=True
    ):
        weights = learned_weights(
            list(slot.strategy_chain),
            {code: float(w) for code, w in (slot.weights or {}).items()},
            counts.get((slot.surface, EventKind.IMPRESSION), {}),
            counts.get((slot.surface, EventKind.ATTACH), {}),
        )
        if weights == slot.weights:
            continue
        slot.weights = weights
        slot.full_clean()
        slot.save(update_fields=["weights", "updated_at"])
        changed += 1
        logger.info(
            "Recommendation weights learned surface=%s weights=%s",
            slot.surface,
            weights,
        )
    return changed
