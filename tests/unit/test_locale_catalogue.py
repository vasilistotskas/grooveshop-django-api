"""The admin is Greek-first: every message in the Greek catalogues is
translated and none is fuzzy (gettext ignores a fuzzy entry, so the
admin shows the English msgid). English is the source language; German
is out of scope for the admin.

Two catalogues: the project's own, and the one for django-unfold's
strings (``manage.py makemessages_unfold``), which Unfold does not ship."""

from __future__ import annotations

import shutil
from pathlib import Path

import polib
import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings

MESSAGES = Path("el") / "LC_MESSAGES" / "django.po"
CATALOGUES = {
    "project": Path(settings.BASE_DIR) / "locale" / MESSAGES,
    "unfold": Path(settings.UNFOLD_LOCALE_PATH) / MESSAGES,
}


def _live_entries(catalogue: Path):
    return [
        entry for entry in polib.pofile(str(catalogue)) if not entry.obsolete
    ]


@pytest.mark.parametrize("catalogue", CATALOGUES.values(), ids=CATALOGUES)
def test_every_greek_message_is_translated(catalogue):
    untranslated = [
        entry.msgid[:80]
        for entry in _live_entries(catalogue)
        if not entry.translated() and "fuzzy" not in entry.flags
    ]
    assert untranslated == []


@pytest.mark.parametrize("catalogue", CATALOGUES.values(), ids=CATALOGUES)
def test_no_greek_message_is_fuzzy(catalogue):
    fuzzy = [
        entry.msgid[:80]
        for entry in _live_entries(catalogue)
        if "fuzzy" in entry.flags
    ]
    assert fuzzy == []


def test_the_unfold_catalogue_matches_the_installed_unfold(tmp_path):
    """An Unfold upgrade that adds or drops strings fails here until
    ``makemessages_unfold -l el`` is run and the new strings translated."""
    copy = tmp_path / MESSAGES
    copy.parent.mkdir(parents=True)
    shutil.copy(CATALOGUES["unfold"], copy)

    with override_settings(UNFOLD_LOCALE_PATH=str(tmp_path)):
        call_command("makemessages_unfold", locale=["el"], verbosity=0)

    def messages(path: Path) -> set[tuple[str | None, str]]:
        return {(entry.msgctxt, entry.msgid) for entry in _live_entries(path)}

    assert messages(copy) == messages(CATALOGUES["unfold"])
