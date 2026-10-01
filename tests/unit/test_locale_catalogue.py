"""The admin is Greek-first: every message in the Greek catalogue is
translated and none is fuzzy (gettext ignores a fuzzy entry, so the
admin shows the English msgid). English is the source language; German
is out of scope for the admin."""

from __future__ import annotations

from pathlib import Path

import polib
from django.conf import settings

CATALOGUE = (
    Path(settings.BASE_DIR) / "locale" / "el" / "LC_MESSAGES" / "django.po"
)


def _live_entries():
    return [
        entry for entry in polib.pofile(str(CATALOGUE)) if not entry.obsolete
    ]


def test_every_greek_message_is_translated():
    untranslated = [
        entry.msgid[:80]
        for entry in _live_entries()
        if not entry.translated() and "fuzzy" not in entry.flags
    ]
    assert untranslated == []


def test_no_greek_message_is_fuzzy():
    fuzzy = [
        entry.msgid[:80] for entry in _live_entries() if "fuzzy" in entry.flags
    ]
    assert fuzzy == []
