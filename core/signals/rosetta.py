"""Rosetta signal handlers.

On every Rosetta edit we do two things:

1. **Mirror each changed entry into the `Translation` table** so the
   database becomes the durable source of truth across deploys. This
   fires on the per-entry `entry_changed` signal — it gives us the
   exact msgids that changed without having to diff .po files.

2. **Bump a Redis version tick** on the per-submit `post_save` signal
   so other pods' TranslationReloadMiddleware notices and re-applies
   the DB overlay to their in-memory catalog. The version key is
   permanent (no TTL race).

The historical disk-bytes-in-Redis sync (`rosetta:po_sync:…`) was
removed in favour of this DB-backed flow — Postgres is authoritative,
disk is transient schema-only.
"""

from __future__ import annotations

import logging
import time

from django.core.cache import cache
from django.db import transaction
from django.dispatch import receiver
from rosetta.signals import entry_changed
from rosetta.signals import post_save as rosetta_post_save

from core.rosetta_storage import (
    TRANSLATION_VERSION_CACHE_KEY,
    _reload_translations,
    apply_db_overlay,
)

logger = logging.getLogger(__name__)


@receiver(entry_changed, dispatch_uid="core.persist_rosetta_entry_to_db")
def persist_rosetta_entry_to_db(
    sender, user, old_msgstr, old_fuzzy, pofile, language_code, **kwargs
):
    """Upsert the changed entry into the Translation table.

    Rosetta fires this *after* mutating the in-memory polib entry but
    *before* saving to disk. Since the DB overlay is our source of
    truth we persist here and don't care whether the disk write that
    follows succeeds or not — a disk failure will self-heal on the
    next overlay apply.

    Handles both singular and plural entries. Context-qualified msgids
    (pgettext) come through with an embedded "\\x04" separator; that
    format is preserved intact to match gettext's catalog key.
    """
    from core.models import Translation

    entry = sender  # polib.POEntry
    msgid = entry.msgid or ""

    try:
        if entry.msgid_plural:
            # entry.msgstr_plural is a dict {index: msgstr}
            with transaction.atomic():
                for plural_index, msgstr in (entry.msgstr_plural or {}).items():
                    Translation.objects.update_or_create(
                        language_code=language_code,
                        msgid=msgid,
                        plural_index=int(plural_index),
                        defaults={
                            "msgid_plural": entry.msgid_plural,
                            "msgstr": msgstr or "",
                        },
                    )
        else:
            Translation.objects.update_or_create(
                language_code=language_code,
                msgid=msgid,
                plural_index=0,
                defaults={
                    "msgid_plural": "",
                    "msgstr": entry.msgstr or "",
                },
            )
    except Exception:
        logger.exception(
            "Failed to persist Rosetta edit for %s / msgid=%r",
            language_code,
            msgid[:80],
        )


@receiver(
    rosetta_post_save, dispatch_uid="core.bump_translation_version_on_save"
)
def bump_translation_version_on_save(
    sender, language_code, request, **kwargs
):
    """Tick the shared version key so other pods refresh their overlay.

    Also applies the overlay locally and clears the gettext cache so
    the pod that handled the Rosetta save serves the new strings
    without waiting for its own middleware on the next request.
    """
    cache.set(TRANSLATION_VERSION_CACHE_KEY, time.time(), timeout=None)
    try:
        apply_db_overlay(language_code=language_code)
        _reload_translations()
    except Exception:
        logger.exception(
            "Local overlay refresh failed after Rosetta save for %s",
            language_code,
        )
