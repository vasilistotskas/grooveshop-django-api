"""Cross-pod translation refresh middleware.

Each pod compares the shared `TRANSLATION_VERSION_CACHE_KEY` in Redis
against its own in-process counter. When they diverge (another pod
saved via Rosetta and bumped the key), this middleware re-applies
the DB overlay and evicts the gettext caches so subsequent lookups
return the fresh msgstrs.

The DB is the source of truth — we never read .po bytes from Redis
or touch disk from here.
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.utils.deprecation import MiddlewareMixin

from core.rosetta_storage import (
    TRANSLATION_VERSION_CACHE_KEY,
    _reload_translations,
    apply_db_overlay,
)

logger = logging.getLogger(__name__)

# Per-process counter; sentinel None forces the first request to sync
# even if the current pod booted after the most recent version tick.
_local_translation_version: float | None = None


class TranslationReloadMiddleware(MiddlewareMixin):
    """Refresh in-memory catalogs when Rosetta edits land on another pod.

    Safe no-op when the Redis key is absent (fresh cluster, no edits
    yet) and when the cache backend raises transiently.
    """

    def process_request(self, request):
        global _local_translation_version

        try:
            remote_version = cache.get(TRANSLATION_VERSION_CACHE_KEY)
        except Exception:
            return None

        if remote_version is None:
            return None

        if _local_translation_version != remote_version:
            try:
                apply_db_overlay()
                _reload_translations()
            except Exception:
                logger.exception(
                    "Failed to refresh translations after version tick %s",
                    remote_version,
                )
                return None
            _local_translation_version = remote_version
            logger.info(
                "Refreshed translations from DB (version %s)", remote_version
            )

        return None
