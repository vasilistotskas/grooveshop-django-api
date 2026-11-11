"""Blank the stock-log reason text a reason code already says.

0088 gave ``StockLog`` a ``reason_code`` and kept the English sentence in
``reason`` beside it, because the release before 0088 still served during
that rollout and read only ``reason``. The release serving now reads
``reason_code`` first (``StockLog.reason_display``) and no code writes the
sentence next to the code any more, so the copy is dead weight in every
manual-adjustment row, frozen in the language of whoever caused it.

Only a row that carries the code AND exactly the sentence is touched; a
row with its own wording is never blanked. ``reason`` is ``blank=True``,
so an empty string is a valid value, and the ``UPDATE`` is idempotent (a
row the serving release writes during the rollout is picked up by a
second run).

The reverse writes the sentence back for a coded row with no text, which
is the shape 0088 left.
"""

from django.db import migrations

MANUAL_TEXT = "Manual stock adjustment via admin panel"
MANUAL_CODE = "manual_adjustment"


def blank_duplicate_text(apps, schema_editor):
    apps.get_model("order", "StockLog").objects.filter(
        reason_code=MANUAL_CODE, reason=MANUAL_TEXT
    ).update(reason="")


def restore_text(apps, schema_editor):
    apps.get_model("order", "StockLog").objects.filter(
        reason_code=MANUAL_CODE, reason=""
    ).update(reason=MANUAL_TEXT)


class Migration(migrations.Migration):
    dependencies = [("order", "0090_order_drop_reminder_columns")]

    operations = [
        migrations.RunPython(blank_duplicate_text, restore_text),
    ]
