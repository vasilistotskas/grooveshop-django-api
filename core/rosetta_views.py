"""Rosetta view override that makes the editor show DB-backed msgstrs.

Rosetta reads msgstrs off disk. After a fresh deploy the on-disk .po
file carries only the msgid *schema* from `makemessages` (msgstrs are
empty). Without this override the Rosetta form would display blank
textareas and clobber the user's DB translations on the next save.

We override the `po_file` property to overlay DB msgstrs onto the
parsed `polib.POFile` before Rosetta hashes its entries. The md5
hashes Rosetta uses to pair form inputs with entries are recomputed
against the DB-overlaid msgstr so the POST round-trip still matches.
"""

from __future__ import annotations

import hashlib
import logging

from django.utils.functional import cached_property
from rosetta.views import TranslationFormView

logger = logging.getLogger(__name__)


class DBBackedTranslationFormView(TranslationFormView):
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
            logger.debug("DB overlay read failed for Rosetta view", exc_info=True)
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
