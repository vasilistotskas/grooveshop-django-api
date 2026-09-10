"""Write the feedback loop's rows.

One ``impression`` row per item shown, one ``click`` per click, one
``attach`` per line of a completed order whose product was shown under
an impression in the same session (Step 2 wires the attach path). All
three share ``impression_id``, minted by the API per response and
echoed by the client.

Called from a Celery task so the write never sits on a page's critical
path; primitives in, count out.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from recommendation.enum import EventKind, StrategyCode, Surface
from recommendation.models import RecommendationEvent

logger = logging.getLogger(__name__)

_KINDS = frozenset(EventKind.values)
_SURFACES = frozenset(Surface.values)
_STRATEGIES = frozenset(StrategyCode.values)


def record_events(
    *,
    kind: str,
    surface: str,
    impression_id: str,
    items: list[dict[str, Any]],
    seed_id: int | None = None,
    session_key: str = "",
    user_id: int | None = None,
) -> int:
    """Insert one row per item; return how many were written.

    Validates the enums again here rather than trusting the caller:
    the task payload crosses a broker, and a row with an unknown kind
    would poison the nightly aggregation with nothing to say why.
    """
    if kind not in _KINDS or surface not in _SURFACES:
        logger.warning(
            "Recommendation event dropped: kind=%r surface=%r", kind, surface
        )
        return 0

    rows = [
        RecommendationEvent(
            kind=kind,
            surface=surface,
            strategy=item["strategy"],
            seed_id=seed_id,
            product_id=int(item["product_id"]),
            position=item.get("position"),
            impression_id=uuid.UUID(str(impression_id)),
            session_key=(session_key or "")[:40],
            user_id=user_id,
        )
        for item in items
        if item.get("strategy") in _STRATEGIES and item.get("product_id")
    ]
    if not rows:
        return 0
    RecommendationEvent.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)
