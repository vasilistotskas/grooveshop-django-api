"""Keep the search index's ``on_offer`` flag in step with promotions.

``on_offer`` is derived from promotions (``promotion.offers``), so it can
go stale without any Product being saved: an admin edits a promotion, a
window opens or closes, a usage limit is reached. Three producers feed
one coalescing queue:

* ``promotion.signals`` — a promotion, its scope, or its usage changed;
* ``reindex_offer_window_changes_task`` (beat, every 5 minutes) — a
  ``starts_at`` / ``ends_at`` passed since the last run.

Producers only record *what may have changed*: promotion ids, or product
ids when the old scope must be captured before it disappears. One task
runs ``REINDEX_DELAY_SECONDS`` after the first mark and drains
everything, so an admin save that fires a dozen signals reindexes once.
The sets live in the cache, scoped per schema by its key function, like
``core.cache.invalidation``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from django_tenants.utils import schema_context

from promotion.models import Promotion
from promotion.offers import scope_product_ids

logger = logging.getLogger(__name__)

#: How long marks are gathered before the one reindex runs.
REINDEX_DELAY_SECONDS = 15

#: Where the window sweep resumes when its cursor is missing (cache
#: flushed, first run). Reindexing is idempotent, so a generous fallback
#: costs a few extra documents; a short one could miss an edge.
WINDOW_FALLBACK = timedelta(days=1)

_PENDING_PROMOTIONS_KEY = "promotion-offer:pending-promotions"
_PENDING_PRODUCTS_KEY = "promotion-offer:pending-products"
_SCHEDULED_KEY = "promotion-offer:scheduled"
_WINDOW_CURSOR_KEY = "promotion-offer:window-cursor"

# Outlive the delay many times over so a slow or restarting worker does
# not lose the marks; the task deletes them when it drains.
_PENDING_TIMEOUT = REINDEX_DELAY_SECONDS * 30
_SCHEDULED_TIMEOUT = REINDEX_DELAY_SECONDS * 6


def indexing_enabled() -> bool:
    """False when Meilisearch is switched off: there is no index to keep."""
    return not settings.MEILISEARCH.get("OFFLINE", False)


def queue_reindex(
    *,
    promotion_ids: Iterable[int] = (),
    product_ids: Iterable[int] = (),
) -> None:
    """Mark promotions / products for reindexing once this transaction commits."""
    promotions = [str(pk) for pk in promotion_ids]
    products = [str(pk) for pk in product_ids]
    if not indexing_enabled() or (not promotions and not products):
        return
    schema_name = getattr(connection, "schema_name", "public")
    transaction.on_commit(lambda: _enqueue(schema_name, promotions, products))


def _enqueue(
    schema_name: str, promotions: list[str], products: list[str]
) -> None:
    # Imported here: ``promotion.tasks`` imports this module.
    from promotion.tasks import reindex_offer_products_task

    try:
        with schema_context(schema_name):
            _add_marks(promotions, products)
            # One task per window: the flag is cleared when it starts.
            if cache.add(_SCHEDULED_KEY, True, timeout=_SCHEDULED_TIMEOUT):
                reindex_offer_products_task.apply_async(
                    countdown=REINDEX_DELAY_SECONDS,
                    headers={"_schema_name": schema_name},
                )
    except Exception:
        # Runs in an on_commit hook: an exception has no caller, and the
        # write that triggered it already succeeded. The nightly full
        # sync repairs whatever this drops.
        logger.exception(
            "Offer reindex could not be queued for schema=%s; on_offer "
            "stays stale until the next full sync",
            schema_name,
        )


def _add_marks(promotions: list[str], products: list[str]) -> None:
    if promotions:
        cache.add_to_set(
            _PENDING_PROMOTIONS_KEY, promotions, timeout=_PENDING_TIMEOUT
        )
    if products:
        cache.add_to_set(
            _PENDING_PRODUCTS_KEY, products, timeout=_PENDING_TIMEOUT
        )


def drain_pending() -> set[int]:
    """Product ids to reindex for every mark queued in this schema."""
    cache.delete(_SCHEDULED_KEY)
    promotions = cache.pop_set(_PENDING_PROMOTIONS_KEY)
    products = cache.pop_set(_PENDING_PRODUCTS_KEY)
    try:
        return {int(pk) for pk in products} | scope_product_ids(
            int(pk) for pk in promotions
        )
    except Exception:
        # Give the marks back so the task's retry still has them.
        _add_marks(sorted(promotions), sorted(products))
        raise


def reindex_window_changes(now: datetime | None = None) -> set[int]:
    """Reindex the products whose promotion window opened or closed.

    A promotion goes live at ``starts_at`` and dies at ``ends_at``
    (``PromotionQuerySet.live``: ``starts_at <= now < ends_at``), so an
    edge in ``(cursor, now]`` is a status change. The cursor only moves
    after the reindex was dispatched; a crash in between repeats the
    window next run instead of skipping it.
    """
    from product.signals import reindex_products_by_pk

    now = now or timezone.now()
    cursor_raw = cache.get(_WINDOW_CURSOR_KEY)
    cursor = (
        datetime.fromisoformat(cursor_raw)
        if cursor_raw
        else now - WINDOW_FALLBACK
    )
    crossed = Promotion.objects.filter(is_active=True).filter(
        Q(starts_at__gt=cursor, starts_at__lte=now)
        | Q(ends_at__gt=cursor, ends_at__lte=now)
    )
    product_ids = scope_product_ids(crossed.values_list("pk", flat=True))
    reindex_products_by_pk(product_ids)
    # No expiry: losing the cursor only widens the next window.
    cache.set(_WINDOW_CURSOR_KEY, now.isoformat(), timeout=None)
    return product_ids
