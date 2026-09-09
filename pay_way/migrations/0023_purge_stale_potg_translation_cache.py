"""Drop the parler cache entry that migration 0022 left behind.

0022 renamed the BOX NOW PAY ON THE GO translation with a queryset
``.update()``. That is the right way to write a data migration — it
touches only matching rows and needs no model instances — but it writes
straight to SQL, past the shared cache that ``PARLER_ENABLE_CACHING``
populates on first read. parler invalidates on ``save()``, via signals
``.update()`` never fires.

So the column was correct the moment 0022 finished and the storefront
kept rendering the OLD name from cache. Measured on staging right after
the v3.48.3 sync: two of three tenants served ``PAY_ON_DELIVERY`` while
the database held ``BOX_NOW_PAY_ON_THE_GO``. That is precisely the bug
0022 exists to fix — two checkout radios both labelled
"Αντικαταβολή" — so without this the fix would have shipped and
appeared not to work.

A migration rather than a documented post-deploy step because the
staleness is per-schema and invisible: nothing fails, a stale label
just sits there until the entry happens to expire. The repair belongs
next to the write that caused it.

One-time and self-limiting: the database is already correct, so every
read after this repopulates the cache correctly.
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.db import migrations
from parler.cache import get_object_cache_keys

logger = logging.getLogger(__name__)

PROVIDER_CODE = "boxnow_pay_on_the_go"


def purge_potg_translation_cache(apps, schema_editor):
    """Evict the cached translations for the rows 0022 rewrote.

    Scoped to the same rows 0022 touched rather than every pay way —
    no other pay-way label was written past the cache.

    ``get_object_cache_keys`` needs the real model: it reads
    ``_parler_meta`` and ``get_available_languages()``, neither of
    which exists on the historical model ``apps.get_model`` returns.
    That is safe here because this migration only deletes cache keys —
    it makes no schema or data assumption that a later migration could
    invalidate.
    """
    db = schema_editor.connection.alias

    try:
        from pay_way.models import PayWay

        keys: list[str] = []
        for pay_way in PayWay.objects.using(db).filter(
            provider_code=PROVIDER_CODE
        ):
            keys.extend(get_object_cache_keys(pay_way))

        if keys:
            cache.delete_many(keys)
        logger.info(
            "Purged %s stale parler cache key(s) for %s",
            len(keys),
            PROVIDER_CODE,
        )
    except Exception as exc:
        # Never fail a deploy over a cache eviction: the database is
        # already right and the entry expires on its own. Logged at
        # WARNING, not swallowed — a silent no-op here would leave the
        # wrong label on screen with nothing to explain it.
        logger.warning(
            "Could not purge the parler cache for %s (%s). The stored "
            "name is correct; the old label may persist until the "
            "cache entry expires.",
            PROVIDER_CODE,
            exc,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("pay_way", "0022_boxnow_potg_distinct_name"),
    ]

    operations = [
        # Reverse is a genuine no-op: un-deleting a cache entry is not
        # a thing, and 0022's own reverse rewrites the row through the
        # same path, so it needs this same repair rather than an
        # inverse of it.
        migrations.RunPython(
            purge_potg_translation_cache,
            migrations.RunPython.noop,
        ),
    ]
