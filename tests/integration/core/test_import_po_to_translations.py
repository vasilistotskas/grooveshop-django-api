"""Protects the durability guarantee of Rosetta edits across deploys.

`import_po_to_translations` is the only command that could plausibly
wipe a saved Rosetta edit — PreSync doesn't call it today, but any
operator running it manually (after a .po merge, for instance) must
not nuke the DB. Covers:

1. additive mode (default) preserves existing Translation rows;
2. additive mode still seeds msgids that have no DB row yet;
3. --force overwrites every row from .po, restoring the old destructive
   behaviour when the operator explicitly asks for it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.core.management import call_command

from core.models import Translation


PO_TEMPLATE = """\
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
"Language: el\\n"

msgid "Order Received - #{order_id}"
msgstr "Παραλάβαμε την παραγγελία σας — #{order_id}"

msgid "Payment Confirmed - Order #{order_id}"
msgstr "Η πληρωμή επιβεβαιώθηκε — Παραγγελία #{order_id}"
"""


def _write_po(tmp_path: Path) -> Path:
    root = tmp_path / "locale" / "el" / "LC_MESSAGES"
    root.mkdir(parents=True)
    po = root / "django.po"
    po.write_text(PO_TEMPLATE, encoding="utf-8")
    return tmp_path / "locale"


@pytest.mark.django_db(transaction=True)
def test_additive_mode_preserves_rosetta_edit(tmp_path, settings):
    """A prior Rosetta edit must survive the command by default."""
    settings.LOCALE_PATHS = [_write_po(tmp_path)]

    Translation.objects.create(
        language_code="el",
        msgid="Order Received - #{order_id}",
        msgid_plural="",
        plural_index=0,
        msgstr="Ρόζετα το έγραψε αυτό",
    )

    call_command("import_po_to_translations", "--language", "el")

    rosetta_row = Translation.objects.get(
        language_code="el", msgid="Order Received - #{order_id}"
    )
    assert rosetta_row.msgstr == "Ρόζετα το έγραψε αυτό", (
        "Additive mode must not overwrite an existing Translation row."
    )


@pytest.mark.django_db(transaction=True)
def test_additive_mode_seeds_missing_msgids(tmp_path, settings):
    """Rows absent from the DB are still imported from .po."""
    settings.LOCALE_PATHS = [_write_po(tmp_path)]

    assert not Translation.objects.filter(
        language_code="el",
        msgid="Payment Confirmed - Order #{order_id}",
    ).exists()

    call_command("import_po_to_translations", "--language", "el")

    created = Translation.objects.get(
        language_code="el",
        msgid="Payment Confirmed - Order #{order_id}",
    )
    assert created.msgstr == "Η πληρωμή επιβεβαιώθηκε — Παραγγελία #{order_id}"


@pytest.mark.django_db(transaction=True)
def test_force_overwrites_rosetta_edit(tmp_path, settings):
    """--force restores the old destructive behaviour on demand."""
    settings.LOCALE_PATHS = [_write_po(tmp_path)]

    Translation.objects.create(
        language_code="el",
        msgid="Order Received - #{order_id}",
        msgid_plural="",
        plural_index=0,
        msgstr="Θα πρέπει να αντικατασταθεί",
    )

    call_command("import_po_to_translations", "--language", "el", "--force")

    row = Translation.objects.get(
        language_code="el", msgid="Order Received - #{order_id}"
    )
    assert row.msgstr == "Παραλάβαμε την παραγγελία σας — #{order_id}", (
        "--force must overwrite the existing msgstr from .po."
    )
