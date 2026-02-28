"""
Middleware that reloads gettext translation catalogs when Rosetta
saves new translations in a multi-replica deployment.

Each pod keeps a local copy of the translation version. On every
request the middleware compares it against the shared version stored
in Redis (set by CacheClearingRosettaStorage). When they differ the
pod writes fresh .po/.mo files from Redis to disk (overwriting any
NFS-stale copies) and then reloads the gettext catalogs.
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.core.cache import cache
from django.utils.deprecation import MiddlewareMixin

from core.rosetta_storage import TRANSLATION_VERSION_CACHE_KEY
from core.rosetta_storage import _reload_translations
from core.signals.rosetta import ROSETTA_FILE_PATHS_KEY
from core.signals.rosetta import ROSETTA_MO_SYNC_PREFIX
from core.signals.rosetta import ROSETTA_PO_SYNC_PREFIX

logger = logging.getLogger(__name__)

# Module-level: each pod process tracks its own known version.
_local_translation_version: float | None = None


def _sync_files_from_redis():
    """Write cached .po/.mo file contents from Redis to the local filesystem."""
    file_paths = cache.get(ROSETTA_FILE_PATHS_KEY)
    if not file_paths:
        return

    for path_hash, po_path in file_paths.items():
        mo_path = po_path.replace(".po", ".mo")

        po_content = cache.get(f"{ROSETTA_PO_SYNC_PREFIX}{path_hash}")
        if po_content:
            try:
                Path(po_path).write_bytes(po_content)
                logger.debug("Wrote synced .po: %s", po_path)
            except OSError:
                logger.error("Failed to write .po: %s", po_path)

        mo_content = cache.get(f"{ROSETTA_MO_SYNC_PREFIX}{path_hash}")
        if mo_content:
            try:
                Path(mo_path).write_bytes(mo_content)
                logger.debug("Wrote synced .mo: %s", mo_path)
            except OSError:
                logger.error("Failed to write .mo: %s", mo_path)


class TranslationReloadMiddleware(MiddlewareMixin):
    """
    On each request, check whether the shared translation version in
    Redis has changed. If so, sync .po/.mo files from Redis to disk
    and reload the gettext catalogs so this pod serves fresh translations.
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
            _sync_files_from_redis()
            _reload_translations()
            _local_translation_version = remote_version
            logger.info("Reloaded translations (version %s)", remote_version)

        return None
