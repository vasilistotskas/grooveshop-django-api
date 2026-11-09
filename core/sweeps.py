"""The window of time a periodic sweep acts on, per store.

A sweep over dates (a promotion window opening, a post going live) asks
for its start, acts on what fell in ``(start, now]``, then advances to
``now``. The position is a ``core.models.SweepCursor`` row, in the
schema the sweep runs in: durable, unlike the cache, which fails open.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db import connection

from core.models import SweepCursor


def sweep_start(name: str, now: datetime, first_window: timedelta) -> datetime:
    """Where sweep ``name`` of the current store resumes: its last
    position, or ``first_window`` before ``now`` on its very first run."""

    position = (
        SweepCursor.objects.filter(
            schema_name=connection.schema_name, name=name
        )
        .values_list("position", flat=True)
        .first()
    )
    return position if position is not None else now - first_window


def advance_sweep(name: str, now: datetime) -> None:
    """Record that sweep ``name`` of the current store acted up to ``now``.

    Call it only after the work succeeded."""

    SweepCursor.objects.update_or_create(
        schema_name=connection.schema_name,
        name=name,
        defaults={"position": now},
    )
