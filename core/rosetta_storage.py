"""
Custom Rosetta storage backend that clears translation cache on save.

Sets a version key in Redis so that all pods in a multi-replica deployment
can detect when translations have changed and reload them.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from rosetta.storage import CacheRosettaStorage

logger = logging.getLogger(__name__)

TRANSLATION_VERSION_CACHE_KEY = "rosetta:translation_version"


class CacheClearingRosettaStorage(CacheRosettaStorage):
    """
    Custom Rosetta storage that bumps a shared translation version in
    the cache (Redis) whenever translations are saved.

    A companion middleware (TranslationReloadMiddleware) checks this
    version on each request and reloads the gettext catalogs when it
    detects a change, ensuring all replicas serve fresh translations.
    """

    def set(self, key: str, val: Any) -> Any:
        result = super().set(key, val)

        try:
            from django.core.cache import cache

            # Bump the shared translation version so all pods pick it up
            cache.set(TRANSLATION_VERSION_CACHE_KEY, time.time(), timeout=None)

            # Also reload translations on this pod immediately
            _reload_translations()

            logger.info("Translation version bumped after Rosetta save")
        except Exception as e:
            logger.error(f"Failed to bump translation version: {e}")

        return result


def _reload_translations():
    """Force Django to reload gettext catalogs from disk."""
    from django.utils.translation import trans_real

    if hasattr(trans_real, "_translations"):
        trans_real._translations = {}
    if hasattr(trans_real, "_default"):
        trans_real._default = None
