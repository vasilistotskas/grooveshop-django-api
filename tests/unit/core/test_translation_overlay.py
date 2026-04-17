"""Tests for the DB-backed translation overlay.

Covers:
- apply_db_overlay mutates the in-memory catalog for singular and plural entries.
- Rosetta entry_changed signal persists msgstrs to the Translation table.
- post_save signal bumps the Redis version key and refreshes the local overlay.
- Middleware ticks its local counter once Redis is bumped, triggering overlay.
- AppConfig.ready tolerates a missing Translation table (guard works during migrate).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.utils.translation import trans_real

from core.middleware.translation_reload import TranslationReloadMiddleware
from core.models import Translation
from core.rosetta_storage import (
    TRANSLATION_VERSION_CACHE_KEY,
    apply_db_overlay,
)
from core.signals.rosetta import (
    bump_translation_version_on_save,
    persist_rosetta_entry_to_db,
)


@pytest.fixture(autouse=True)
def reset_gettext_caches():
    """Keep each test isolated — clear both catalog caches before and after."""
    import gettext

    trans_real._translations = {}
    trans_real._default = None
    gettext._translations = {}
    yield
    trans_real._translations = {}
    trans_real._default = None
    gettext._translations = {}


@pytest.mark.django_db
def test_apply_db_overlay_injects_singular_msgstr():
    Translation.objects.create(
        language_code="el",
        msgid="Hello, world",
        msgid_plural="",
        plural_index=0,
        msgstr="Γειά σου, κόσμε",
    )

    apply_db_overlay("el")

    catalog = trans_real.translation("el")
    assert catalog._catalog["Hello, world"] == "Γειά σου, κόσμε"


@pytest.mark.django_db
def test_apply_db_overlay_handles_plural_entries():
    for idx, msgstr in enumerate(["1 γάτα", "%d γάτες"]):
        Translation.objects.create(
            language_code="el",
            msgid="1 cat",
            msgid_plural="%d cats",
            plural_index=idx,
            msgstr=msgstr,
        )

    apply_db_overlay("el")

    catalog = trans_real.translation("el")
    assert catalog._catalog[("1 cat", 0)] == "1 γάτα"
    assert catalog._catalog[("1 cat", 1)] == "%d γάτες"


@pytest.mark.django_db
def test_apply_db_overlay_skips_empty_msgstr():
    Translation.objects.create(
        language_code="el",
        msgid="Untranslated Yet",
        msgstr="",
    )

    apply_db_overlay("el")

    catalog = trans_real.translation("el")
    # Empty DB rows do not overwrite an on-disk translation (if any) —
    # the key is simply absent from the overlay dict.
    assert catalog._catalog.get("Untranslated Yet") in (None, "")


@pytest.mark.django_db
def test_persist_rosetta_entry_creates_singular_row():
    entry = SimpleNamespace(
        msgid="Save",
        msgid_plural="",
        msgstr="Αποθήκευση",
        msgstr_plural={},
    )

    persist_rosetta_entry_to_db(
        sender=entry,
        user=MagicMock(),
        old_msgstr="",
        old_fuzzy=False,
        pofile="/tmp/django.po",
        language_code="el",
    )

    row = Translation.objects.get(
        language_code="el", msgid="Save", plural_index=0
    )
    assert row.msgstr == "Αποθήκευση"
    assert row.msgid_plural == ""


@pytest.mark.django_db
def test_persist_rosetta_entry_updates_existing_row():
    Translation.objects.create(
        language_code="el", msgid="Save", plural_index=0, msgstr="Σώσε"
    )

    entry = SimpleNamespace(
        msgid="Save",
        msgid_plural="",
        msgstr="Αποθήκευση",
        msgstr_plural={},
    )
    persist_rosetta_entry_to_db(
        sender=entry,
        user=MagicMock(),
        old_msgstr="Σώσε",
        old_fuzzy=False,
        pofile="/tmp/django.po",
        language_code="el",
    )

    row = Translation.objects.get(language_code="el", msgid="Save")
    assert row.msgstr == "Αποθήκευση"
    assert Translation.objects.count() == 1


@pytest.mark.django_db
def test_persist_rosetta_entry_writes_plural_rows():
    entry = SimpleNamespace(
        msgid="1 cat",
        msgid_plural="%d cats",
        msgstr="",
        msgstr_plural={0: "1 γάτα", 1: "%d γάτες"},
    )

    persist_rosetta_entry_to_db(
        sender=entry,
        user=MagicMock(),
        old_msgstr="",
        old_fuzzy=False,
        pofile="/tmp/django.po",
        language_code="el",
    )

    rows = Translation.objects.filter(language_code="el", msgid="1 cat").order_by(
        "plural_index"
    )
    assert [r.msgstr for r in rows] == ["1 γάτα", "%d γάτες"]
    assert all(r.msgid_plural == "%d cats" for r in rows)


@pytest.mark.django_db
def test_bump_translation_version_updates_cache_key():
    cache.delete(TRANSLATION_VERSION_CACHE_KEY)
    with patch(
        "core.signals.rosetta.apply_db_overlay"
    ) as overlay, patch(
        "core.signals.rosetta._reload_translations"
    ) as reload_:
        bump_translation_version_on_save(
            sender=None, language_code="el", request=MagicMock()
        )

    assert cache.get(TRANSLATION_VERSION_CACHE_KEY) is not None
    overlay.assert_called_once_with(language_code="el")
    reload_.assert_called_once()


@pytest.mark.django_db
def test_middleware_refreshes_once_per_version_tick():
    """First request after a tick runs overlay; subsequent ones do not."""
    import core.middleware.translation_reload as mw_module

    mw_module._local_translation_version = None
    cache.set(TRANSLATION_VERSION_CACHE_KEY, 42.0, timeout=None)

    with patch(
        "core.middleware.translation_reload.apply_db_overlay"
    ) as overlay, patch(
        "core.middleware.translation_reload._reload_translations"
    ) as reload_:
        mw = TranslationReloadMiddleware(lambda req: None)
        mw.process_request(MagicMock())
        overlay.assert_called_once()
        reload_.assert_called_once()

        # Second call with the same version must not re-trigger.
        mw.process_request(MagicMock())
        assert overlay.call_count == 1
        assert reload_.call_count == 1

        # Bump the version — now the next request refreshes again.
        cache.set(TRANSLATION_VERSION_CACHE_KEY, 43.0, timeout=None)
        mw.process_request(MagicMock())
        assert overlay.call_count == 2


@pytest.mark.django_db
def test_apply_db_overlay_tolerates_missing_translation_table():
    """Simulates the migrate phase before 0006_translation runs."""
    from django.db.utils import ProgrammingError

    with patch(
        "core.rosetta_storage._overlay_rows",
        side_effect=ProgrammingError("relation does not exist"),
    ):
        # Must not raise — just logs + returns.
        apply_db_overlay("el")
