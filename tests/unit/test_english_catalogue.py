"""English is served to customers on stores that offer it, so its catalogue
must be complete: every live message has an English msgstr and none is
fuzzy (gettext ignores a fuzzy entry, and a guessed one is worse than the
msgid it replaced). German is not served to any store and is not judged."""

from __future__ import annotations

from pathlib import Path

import polib
from django.conf import settings

CATALOGUE = (
    Path(settings.BASE_DIR) / "locale" / "en" / "LC_MESSAGES" / "django.po"
)


def _live_entries():
    return [
        entry for entry in polib.pofile(str(CATALOGUE)) if not entry.obsolete
    ]


def test_every_english_message_has_an_english_msgstr():
    missing = [
        entry.msgid[:80]
        for entry in _live_entries()
        if not entry.translated() and "fuzzy" not in entry.flags
    ]
    assert missing == []


def test_no_english_message_is_fuzzy():
    fuzzy = [
        entry.msgid[:80] for entry in _live_entries() if "fuzzy" in entry.flags
    ]
    assert fuzzy == []
    assert "fuzzy" not in polib.pofile(str(CATALOGUE)).metadata_is_fuzzy


def test_the_english_header_names_the_language():
    metadata = polib.pofile(str(CATALOGUE)).metadata
    assert metadata["Language"] == "en"
