"""Write the feedback loop's rows.

One ``impression`` row per item shown, one ``click`` per click — both
reported by the storefront — and one ``attach`` per order line whose
product had been shown under an impression to the same cart or the same
signed-in customer inside the attribution windows. All three share
``impression_id``, minted by the API per response and echoed by the
client.

Called from Celery tasks so no write sits on a page's or a checkout's
critical path; primitives in, count out.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db.models import Q

from recommendation.enum import AttachMatch, EventKind, StrategyCode, Surface
from recommendation.models import RecommendationEvent

logger = logging.getLogger(__name__)

_KINDS = frozenset(EventKind.values)
_SURFACES = frozenset(Surface.values)
_STRATEGIES = frozenset(StrategyCode.values)


def _as_uuid(value: Any) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError, TypeError, AttributeError:
        return None


def record_events(
    *,
    kind: str,
    surface: str,
    impression_id: str,
    items: list[dict[str, Any]],
    seed_id: int | None = None,
    session_key: str = "",
    user_id: int | None = None,
    cart_uuid: str | None = None,
) -> int:
    """Insert one row per item; return how many were written.

    Validates the enums again here rather than trusting the caller:
    the task payload crosses a broker, and a row with an unknown kind
    would poison the nightly aggregation with nothing to say why.
    ``attach`` is never accepted from here — it is derived from orders.
    """
    if (
        kind not in _KINDS
        or kind == EventKind.ATTACH
        or surface not in _SURFACES
    ):
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
            cart_uuid=_as_uuid(cart_uuid),
        )
        for item in items
        if item.get("strategy") in _STRATEGIES and item.get("product_id")
    ]
    if not rows:
        return 0
    RecommendationEvent.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def record_attach_events(order_id: int) -> int:
    """Tie a new order's lines back to the impressions that showed them.

    For each line, every impression of that product shown before the
    order to the SAME CART (``metadata.cart_snapshot.cart_uuid``) within
    ``RECOMMENDATION_ATTACH_CART_WINDOW_HOURS``, or to the SAME SIGNED-IN
    CUSTOMER within ``RECOMMENDATION_ATTACH_USER_WINDOW_DAYS``, yields one
    ``attach`` row carrying the impression's surface, strategy and
    position — that is what attach rate per strategy is computed from —
    and ``matched_by`` says which window caught it, the cart taking
    precedence when both do.

    Idempotent: the unique constraint on (order, product, impression)
    makes a retried task a no-op. Returns the number of rows written.
    """
    from order.models.order import Order

    # A full row, not ``.only()``: ``Order.__init__`` reads ``status`` to
    # track transitions, and a deferred field there re-fetches.
    order = Order.objects.filter(pk=order_id).first()
    if order is None:
        logger.warning("Attach skipped: order %s does not exist", order_id)
        return 0
    product_ids = list(order.items.values_list("product_id", flat=True))
    if not product_ids:
        return 0

    snapshot = (order.metadata or {}).get("cart_snapshot") or {}
    cart_uuid = _as_uuid(snapshot.get("cart_uuid"))
    placed_at = order.created_at
    cart_since = placed_at - timedelta(
        hours=settings.RECOMMENDATION_ATTACH_CART_WINDOW_HOURS
    )
    user_since = placed_at - timedelta(
        days=settings.RECOMMENDATION_ATTACH_USER_WINDOW_DAYS
    )

    match = Q()
    if cart_uuid is not None:
        match |= Q(cart_uuid=cart_uuid, created_at__gte=cart_since)
    if order.user_id is not None:
        match |= Q(user_id=order.user_id, created_at__gte=user_since)
    if not match:
        # An order with neither identity (an agent-placed order with no
        # cart, a guest whose snapshot is missing) has nothing to match.
        return 0

    impressions = (
        RecommendationEvent.objects.filter(
            match,
            kind=EventKind.IMPRESSION,
            product_id__in=product_ids,
            created_at__lte=placed_at,
        )
        .order_by("-created_at")
        .only(
            "surface",
            "strategy",
            "seed_id",
            "product_id",
            "position",
            "impression_id",
            "session_key",
            "cart_uuid",
            "created_at",
        )
    )

    seen: set[tuple[int, uuid.UUID]] = set()
    rows: list[RecommendationEvent] = []
    for impression in impressions:
        key = (impression.product_id, impression.impression_id)
        if key in seen:
            continue
        seen.add(key)
        same_cart = (
            cart_uuid is not None
            and impression.cart_uuid == cart_uuid
            and impression.created_at >= cart_since
        )
        rows.append(
            RecommendationEvent(
                kind=EventKind.ATTACH,
                surface=impression.surface,
                strategy=impression.strategy,
                seed_id=impression.seed_id,
                product_id=impression.product_id,
                position=impression.position,
                impression_id=impression.impression_id,
                session_key=impression.session_key,
                user_id=order.user_id,
                cart_uuid=impression.cart_uuid,
                order_id=order.id,
                matched_by=AttachMatch.CART if same_cart else AttachMatch.USER,
            )
        )
    if not rows:
        return 0
    RecommendationEvent.objects.bulk_create(rows, ignore_conflicts=True)
    logger.info(
        "Recommendation attach recorded order=%s lines=%s rows=%s",
        order.id,
        len(product_ids),
        len(rows),
    )
    return len(rows)
