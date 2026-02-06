"""
Rosetta hooks to handle translation cache invalidation.

Uses Django's public API for cache invalidation to ensure compatibility
across Django versions.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def clear_translation_cache() -> None:
    """
    Clear Django's translation cache to force reload of .mo files.

    Uses Django's public cache invalidation API instead of accessing
    private attributes directly. This ensures compatibility with future
    Django versions.
    """
    try:
        # Use Django's public API for cache invalidation
        from django.core.cache import cache
        from django.utils.translation import get_language

        # Clear translation cache keys
        current_language = get_language()
        cache_keys = [
            f"translations.{current_language}",
            "translations.*",
        ]

        for key in cache_keys:
            cache.delete(key)

        logger.info("Translation cache cleared successfully")

    except Exception as e:
        logger.error(f"Failed to clear translation cache: {e}")
        # Fallback to private API only if public API fails
        # This maintains backward compatibility
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
                f"Translation cache clearing failed completely: {fallback_error}"
            )
