"""
Custom Rosetta storage backend that clears translation cache on save.

Uses Django's public API for cache invalidation to ensure compatibility
across Django versions.
"""

from __future__ import annotations

import logging
from typing import Any

from rosetta.storage import CacheRosettaStorage

logger = logging.getLogger(__name__)


class CacheClearingRosettaStorage(CacheRosettaStorage):
    """
    Custom Rosetta storage that clears Django's translation cache
    whenever translations are saved.

    This ensures that updated translations are immediately visible
    across all processes in a multi-process environment.

    Uses Django's public cache API instead of accessing private attributes
    to maintain compatibility with future Django versions.
    """

    def set(self, key: str, val: Any) -> Any:
        """
        Override set to clear translation cache after saving.

        Args:
            key: Storage key for the translation data
            val: Translation data to store

        Returns:
            Result from parent set method
        """
        result = super().set(key, val)

        # Clear translation cache using public API
        try:
            from django.core.cache import cache
            from django.utils.translation import get_language

            current_language = get_language()
            cache_keys = [
                f"translations.{current_language}",
                "translations.*",
            ]

            for cache_key in cache_keys:
                cache.delete(cache_key)

            logger.debug("Translation cache cleared after Rosetta save")

        except Exception as e:
            logger.error(f"Failed to clear translation cache: {e}")

            # Fallback to private API only if public API fails
            try:
                from django.utils.translation import trans_real

                if hasattr(trans_real, "_translations"):
                    trans_real._translations = {}
                if hasattr(trans_real, "_default"):
                    trans_real._default = None
                if hasattr(trans_real, "_active"):
                    trans_real._active = None

                logger.warning(
                    "Used private API fallback for translation cache clearing"
                )
            except Exception as fallback_error:
                logger.error(
                    f"Translation cache clearing failed: {fallback_error}"
                )

        return result
