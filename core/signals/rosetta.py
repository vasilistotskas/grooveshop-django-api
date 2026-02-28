"""
Rosetta post_save signal handler for syncing .po/.mo files across K8s replicas.

When a translation is saved via Rosetta on one pod, the .po and .mo files
are written to disk. In a multi-replica deployment with NFS (ReadWriteMany),
other pods may read stale files due to NFS attribute caching. This handler
stores the file contents in Redis so that the TranslationReloadMiddleware
on other pods can write fresh copies to their local filesystem view.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from django.core.cache import cache
from django.dispatch import receiver
from rosetta.poutil import find_pos
from rosetta.signals import post_save as rosetta_post_save

from core.rosetta_storage import TRANSLATION_VERSION_CACHE_KEY
from core.rosetta_storage import _reload_translations

logger = logging.getLogger(__name__)

ROSETTA_FILE_PATHS_KEY = "rosetta:file_paths"
ROSETTA_PO_SYNC_PREFIX = "rosetta:po_sync:"
ROSETTA_MO_SYNC_PREFIX = "rosetta:mo_sync:"

# Keep synced files in Redis for 24 hours (safety expiry)
SYNC_TIMEOUT = 86400


def _path_hash(path: str) -> str:
    return hashlib.md5(path.encode()).hexdigest()


def _resolve_po_path(language_code: str, request) -> str | None:
    """Reconstruct the .po file path from Rosetta URL kwargs."""
    if not request.resolver_match:
        return None

    kwargs = request.resolver_match.kwargs
    po_filter = kwargs.get("po_filter", "project")
    try:
        idx = int(kwargs.get("idx", 0))
    except (TypeError, ValueError):
        return None

    third_party_apps = po_filter in ("all", "third-party")
    django_apps = po_filter in ("all", "django")
    project_apps = po_filter in ("all", "project")

    po_paths = find_pos(
        language_code,
        project_apps=project_apps,
        django_apps=django_apps,
        third_party_apps=third_party_apps,
    )
    # Rosetta sorts by app name (dir before /locale), not full path
    po_paths.sort(key=lambda p: p.split("/locale")[0].split("/")[-1])

    if idx < len(po_paths):
        return po_paths[idx]
    return None


@receiver(rosetta_post_save)
def sync_translation_files_to_redis(sender, language_code, request, **kwargs):
    """Store the saved .po and .mo file contents in Redis for cross-pod sync."""
    po_path = _resolve_po_path(language_code, request)
    if not po_path:
        logger.warning(
            "Could not resolve .po file path from Rosetta post_save signal"
        )
        return

    mo_path = po_path.replace(".po", ".mo")
    ph = _path_hash(po_path)

    try:
        po_content = Path(po_path).read_bytes()
        cache.set(f"{ROSETTA_PO_SYNC_PREFIX}{ph}", po_content, SYNC_TIMEOUT)
        logger.info("Synced .po to Redis: %s", po_path)
    except FileNotFoundError:
        logger.error("Cannot read .po file for sync: %s", po_path)
        return

    try:
        mo_content = Path(mo_path).read_bytes()
        cache.set(f"{ROSETTA_MO_SYNC_PREFIX}{ph}", mo_content, SYNC_TIMEOUT)
        logger.info("Synced .mo to Redis: %s", mo_path)
    except FileNotFoundError:
        logger.warning("No .mo file to sync: %s", mo_path)

    # Maintain a mapping of path_hash -> absolute_path
    file_paths = cache.get(ROSETTA_FILE_PATHS_KEY) or {}
    file_paths[ph] = po_path
    cache.set(ROSETTA_FILE_PATHS_KEY, file_paths, timeout=None)

    # Bump the shared translation version so all pods pick up the change.
    # This must happen here (not in CacheClearingRosettaStorage.set()) because
    # Rosetta writes .po files directly to disk — storage.set() is only called
    # for internal cache state, not during the actual translation save.
    cache.set(TRANSLATION_VERSION_CACHE_KEY, time.time(), timeout=None)
    _reload_translations()
    logger.info("Translation version bumped after Rosetta save")
