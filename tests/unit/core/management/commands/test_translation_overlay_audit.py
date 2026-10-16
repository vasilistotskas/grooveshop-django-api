"""`translation_overlay_audit` is the only window onto the DB overlay,
so it has to classify honestly and delete nothing it was not told to.

The catalogue is stubbed with a plain dict — `TranslationCatalog.get`
is the whole interface the command uses, and the tests must not depend
on a compiled .mo being present on the machine running them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import Translation
from core.rosetta_storage import TRANSLATION_VERSION_CACHE_KEY

pytestmark = pytest.mark.django_db

CATALOG = {
    "Order Received": "Παραλάβαμε την παραγγελία σας",
    "Save": "Αποθήκευση",
    ("1 cat", 1): "%d γάτες",
}


@pytest.fixture(autouse=True)
def stub_catalog():
    with patch(
        "core.management.commands.translation_overlay_audit._disk_catalog",
        return_value=CATALOG,
    ):
        yield


@pytest.fixture(autouse=True)
def clear_version_key():
    cache.delete(TRANSLATION_VERSION_CACHE_KEY)
    yield
    cache.delete(TRANSLATION_VERSION_CACHE_KEY)


@pytest.fixture
def rows():
    return SimpleNamespace(
        custom=Translation.objects.create(
            language_code="el",
            msgid="Order Received",
            msgstr="Η Παραγγελία Παραδόθηκε",
        ),
        redundant=Translation.objects.create(
            language_code="el", msgid="Save", msgstr="Αποθήκευση"
        ),
        redundant_plural=Translation.objects.create(
            language_code="el",
            msgid="1 cat",
            msgid_plural="%d cats",
            plural_index=1,
            msgstr="%d γάτες",
        ),
        untranslated=Translation.objects.create(
            language_code="el", msgid="Brand new", msgstr="Ολοκαίνουργιο"
        ),
    )


def _run(*args: str) -> str:
    out = StringIO()
    call_command("translation_overlay_audit", "-l", "el", *args, stdout=out)
    return out.getvalue()


def _remaining() -> set[str]:
    return set(Translation.objects.values_list("msgid", flat=True))


def test_reports_every_class_and_lists_only_the_custom_rows(rows):
    output = _run()

    assert "el: 4 rows — 2 custom, 2 redundant" in output
    assert f"#{rows.custom.id}" in output
    assert "db: 'Η Παραγγελία Παραδόθηκε'" in output
    assert "served: 'Παραλάβαμε την παραγγελία σας'" in output
    # A key the .po leaves untranslated serves its msgid, so a row
    # that replaces it is doing work too.
    assert f"#{rows.untranslated.id}" in output
    assert "served: 'Brand new'" in output
    # Rows that change nothing are a number, not a listing.
    assert "'Save'" not in output
    # A bare audit is read-only.
    assert Translation.objects.count() == 4
    assert cache.get(TRANSLATION_VERSION_CACHE_KEY) is None


def test_prune_deletes_only_the_redundant_rows(rows):
    output = _run("--prune")

    assert "Deleted 2 overlay row(s)" in output
    assert _remaining() == {"Order Received", "Brand new"}
    assert cache.get(TRANSLATION_VERSION_CACHE_KEY) is not None


def test_drop_ids_deletes_exactly_the_named_rows(rows):
    _run("--drop-ids", str(rows.custom.id), str(rows.untranslated.id))

    assert _remaining() == {"Save", "1 cat"}
    assert cache.get(TRANSLATION_VERSION_CACHE_KEY) is not None


def test_drop_before_deletes_the_rows_older_than_the_cutoff(rows):
    # `updated_at` is auto_now; a queryset update is the only way to
    # back-date it, which is also how a stale bulk import looks.
    Translation.objects.filter(pk=rows.custom.pk).update(
        updated_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC)
    )

    _run("--drop-before", "2026-04-20")

    assert _remaining() == {"Save", "1 cat", "Brand new"}
    assert cache.get(TRANSLATION_VERSION_CACHE_KEY) is not None


def test_an_unknown_language_is_refused():
    with pytest.raises(CommandError, match="Unknown language"):
        call_command("translation_overlay_audit", "-l", "xx", stdout=StringIO())


def test_a_source_language_row_that_rewrites_the_msgid_is_custom():
    """English keys are untranslated in django.po, so the catalogue
    serves the msgid. A row that changes it is listed, and one that
    repeats it is redundant (production 2026-10-16: "Order Received"
    served as "Order Delivered" to English shoppers)."""
    wrong = Translation.objects.create(
        language_code="en", msgid="Order Received", msgstr="Order Delivered"
    )
    Translation.objects.create(language_code="en", msgid="Save", msgstr="Save")
    plural = Translation.objects.create(
        language_code="en",
        msgid="1 cat",
        msgid_plural="%d cats",
        plural_index=1,
        msgstr="%d cats",
    )
    out = StringIO()
    with patch(
        "core.management.commands.translation_overlay_audit._disk_catalog",
        return_value={},
    ):
        call_command("translation_overlay_audit", "-l", "en", stdout=out)
    output = out.getvalue()

    assert "en: 3 rows — 1 custom, 2 redundant" in output
    assert f"#{wrong.id}" in output
    assert "served: 'Order Received'" in output
    assert f"#{plural.id}" not in output
