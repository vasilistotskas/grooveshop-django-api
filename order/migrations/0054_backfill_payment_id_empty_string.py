"""Collapse the two spellings of "this order has no payment id".

Step ONE of a two-release change — see
``search/migrations/0004_alter_searchquery_language_code_and_more`` for the
full reasoning on why the ``NOT NULL`` constraint has to wait for the next
release under the PreSync deploy model.

``payment_id`` is the worst of the thirteen ``DJ001`` sites, because it
already carries ``default=""`` **and** ``null=True``: new rows get "",
older ones got NULL, and ``order_payment_id_ix`` indexes a column whose
"empty" value depends on when the row was written. No model change is
needed here — the default has been right for a while — so this migration
is the backfill alone.
"""

from django.db import migrations

from core.db.migration_operations import BackfillNullStringsToEmpty


class Migration(migrations.Migration):
    # The backfill commits per batch; one transaction around all
    # of them would hold every row lock to the end.
    atomic = False

    dependencies = [
        ("order", "0053_order_metadata_gin_indexes"),
    ]

    operations = [
        BackfillNullStringsToEmpty(
            "order",
            ["payment_id"],
        ),
    ]
