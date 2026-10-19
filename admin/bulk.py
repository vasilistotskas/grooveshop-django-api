"""Bulk admin edits that still go through ``save``."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import QuerySet


def update_each(queryset: QuerySet, **values: Any) -> int:
    """Set *values* on every row of *queryset* through ``save``.

    ``queryset.update`` is one UPDATE statement: it emits no ``post_save``,
    so the storefront cache purge (``core.cache.invalidation``), the
    Meilisearch reindex, the price refresh a promotion queues and the
    simple-history row all stayed behind, and the storefront served the old
    state until its TTL. Saving each row sends the signals the single-row
    admin edit sends.

    Rows already holding every value are skipped and not counted, so a
    repeated action neither purges nor writes history. Rows are saved in pk
    order, inside one transaction, so the purge is queued once on commit and
    two bulk edits (or a checkout) over shared rows lock in the same order.
    Returns the number of rows changed.
    """
    fields = list(values)
    changed = 0
    with transaction.atomic():
        for obj in queryset.exclude(**values).order_by("pk"):
            for name, value in values.items():
                setattr(obj, name, value)
            obj.save(update_fields=fields)
            changed += 1
    return changed
