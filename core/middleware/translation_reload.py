"""
Middleware that reloads gettext translation catalogs when Rosetta
saves new translations in a multi-replica deployment.

Each pod keeps a local copy of the translation version. On every
request the middleware compares it against the shared version stored
in Redis (set by CacheClearingRosettaStorage). When they differ the
pod reloads translations from the .mo files on the shared volume.
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.utils.deprecation import MiddlewareMixin

from core.rosetta_storage import TRANSLATION_VERSION_CACHE_KEY
from core.rosetta_storage import _reload_translations

logger = logging.getLogger(__name__)

# Module-level: each pod process tracks its own known version.
_local_translation_version: float | None = None


class TranslationReloadMiddleware(MiddlewareMixin):
    """
    On each request, check whether the shared translation version in
    Redis has changed. If so, reload the gettext catalogs so this pod
    starts serving the latest translations.
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
            _reload_translations()
            _local_translation_version = remote_version
            logger.info("Reloaded translations (version %s)", remote_version)

        return None
