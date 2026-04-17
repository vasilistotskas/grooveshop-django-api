"""Rosetta view override that makes the editor DB-backed end-to-end.

Two overrides are load-bearing:

1. `po_file_is_writable` is forced to `False` so Rosetta's view never
   writes `.po`/`.mo` bytes back to the image-baked locale path on
   save. Disk writes would (a) fail on the read-only image layer
   anyway, (b) create cross-pod drift because PVC updates are stale,
   (c) clobber the DB overlay that our signals already captured.
   With this flag off, Rosetta routes its edits through its storage
   cache (Redis) for the editing session only; the durable state
   lives in `Translation` rows via `persist_rosetta_entry_to_db`.

2. `po_file` overlays current DB msgstrs onto the parsed
   `polib.POFile` before Rosetta hashes entries, so the form always
   displays the live DB state (not the empty-msgid shell from the
   image). The md5 hash Rosetta uses to match form inputs to entries
   is recomputed against the DB-overlaid msgstr so the POST round-
   trip still pairs up correctly.
"""

from __future__ import annotations

import hashlib
import logging

from django.utils.functional import cached_property
from rosetta.views import TranslationFormView

logger = logging.getLogger(__name__)


class DBBackedTranslationFormView(TranslationFormView):
    @cached_property
    def po_file_is_writable(self) -> bool:
        # Forced False — see module docstring. Rosetta's non-writable
        # code path uses its storage cache for the edit session and
        # skips the on-disk save; our entry_changed signal persists
        # each edit to the Translation table regardless, so nothing
        # is lost.
        return False

    @cached_property
    def po_file(self):
        po = super().po_file

        language_code = self.kwargs.get("lang_id")
        if not language_code:
            return po

        try:
            from core.models import Translation
        except Exception:
            return po

        # Fetch every DB row for the language in one query and index by
        # (msgid, plural_index) so the entry walk is O(N_entries).
        try:
            overlay = {
                (row["msgid"], row["plural_index"]): row["msgstr"]
                for row in Translation.objects.filter(
                    language_code=language_code
                ).values("msgid", "plural_index", "msgstr")
            }
        except Exception:
            logger.debug(
                "DB overlay read failed for Rosetta view", exc_info=True
            )
            return po

        if not overlay:
            return po

        changed = 0
        for entry in po:
            if entry.msgid_plural:
                # Plural entry — walk every index present in DB.
                new_plural = dict(entry.msgstr_plural or {})
                for (msgid, plural_index), msgstr in overlay.items():
                    if msgid == entry.msgid:
                        new_plural[plural_index] = msgstr
                if new_plural != (entry.msgstr_plural or {}):
                    entry.msgstr_plural = new_plural
                    changed += 1
                    _rehash(entry)
            else:
                db_msgstr = overlay.get((entry.msgid, 0))
                if db_msgstr is not None and db_msgstr != entry.msgstr:
                    entry.msgstr = db_msgstr
                    changed += 1
                    _rehash(entry)

        if changed:
            logger.debug(
                "Rosetta view overlaid %d DB msgstrs for %s",
                changed,
                language_code,
            )
        return po


def _rehash(entry) -> None:
    """Recompute the md5hash Rosetta uses to match form fields to entries.

    Mirrors the computation in rosetta.views.RosettaFileLevelMixin.po_file
    (msgid + msgstr + msgctxt). Must run whenever we change the entry's
    msgstr after Rosetta's own hash pass.
    """
    str_to_hash = (
        str(entry.msgid) + str(entry.msgstr) + str(entry.msgctxt or "")
    ).encode("utf8")
    entry.md5hash = hashlib.md5(str_to_hash).hexdigest()
