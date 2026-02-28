"""
Custom Rosetta storage backend.

The actual translation version bumping and cross-pod sync happens in
core.signals.rosetta (connected to Rosetta's post_save signal), not here.
"""

from __future__ import annotations

import logging

from rosetta.storage import CacheRosettaStorage

logger = logging.getLogger(__name__)

TRANSLATION_VERSION_CACHE_KEY = "rosetta:translation_version"


class CacheClearingRosettaStorage(CacheRosettaStorage):
    """
    Custom Rosetta storage that extends CacheRosettaStorage.

    Translation version bumping and reload are handled by the
    post_save signal handler in core.signals.rosetta, which fires
    when Rosetta actually saves .po files to disk.
    """


def _reload_translations():
    """Force Django to reload gettext catalogs from disk."""
    from django.utils.translation import trans_real

    if hasattr(trans_real, "_translations"):
        trans_real._translations = {}
    if hasattr(trans_real, "_default"):
        trans_real._default = None
