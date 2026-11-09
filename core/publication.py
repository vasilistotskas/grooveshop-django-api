"""What happens when a publish date passes.

A ``PublishableModel`` row saved with a future ``published_at`` is served
from that date (``published()``: ``is_published`` and ``published_at <=
now``) with no write at that moment. Nothing that listens to ``post_save``
notices: the cache surfaces that render the row keep the answer they cached
before the date, and IndexNow never hears of the page.

``release_due_publications`` is what notices. Run every minute per store, it
finds the published rows whose date fell in ``(cursor, now]`` and does what a
save at that moment would have done: it marks stale every surface that
declares the row's model in ``invalidated_by``, and sends ``went_live`` for
each row (IndexNow queues the page). A row saved on or after its date is left
out: that save already did both.

The window is a durable per-store cursor (``core.sweeps``) that moves only
after the work ran, so a run that fails repeats its window instead of
skipping it, the same contract as
``promotion.offer_sync.refresh_window_changes``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.apps import apps
from django.db.models import F, Model
from django.dispatch import Signal
from django.utils import timezone

from core.cache.invalidation import mark_surface_stale, surfaces_invalidated_by
from core.models import PublishableModel
from core.sweeps import advance_sweep, sweep_start

logger = logging.getLogger(__name__)

#: Sent once for each row whose publish date passed with no save at that
#: moment. ``sender`` is the model, ``instance`` the row.
went_live = Signal()

#: How far back a store's very first run looks.
FIRST_WINDOW = timedelta(days=1)

#: The sweep's name in ``core.models.SweepCursor``.
SWEEP_NAME = "publication-go-live"


def publishable_models() -> list[type[Model]]:
    """Every concrete model with a publish date."""

    return [
        model
        for model in apps.get_models()
        if issubclass(model, PublishableModel)
    ]


def release_due_publications(now: datetime | None = None) -> int:
    """Act on the rows whose publish date passed since the last run.

    Returns how many rows went live.
    """

    now = now or timezone.now()
    cursor = sweep_start(SWEEP_NAME, now, FIRST_WINDOW)

    released = 0
    for model in publishable_models():
        due = model._default_manager.filter(
            is_published=True,
            published_at__gt=cursor,
            published_at__lte=now,
        )
        if any(field.name == "updated_at" for field in model._meta.fields):
            # Saved on or after its date: that save purged and announced.
            due = due.filter(updated_at__lt=F("published_at"))
        rows = list(due)
        if not rows:
            continue

        for code in surfaces_invalidated_by(model):
            mark_surface_stale(code)
        for row in rows:
            went_live.send(sender=model, instance=row)
        released += len(rows)
        logger.info(
            "Publication sweep: %s %s went live",
            len(rows),
            model._meta.label,
        )

    advance_sweep(SWEEP_NAME, now)
    return released
