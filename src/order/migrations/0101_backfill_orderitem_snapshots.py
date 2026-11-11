"""Give existing order lines the name and SKU of their product.

Split from the ADD COLUMN migration on purpose, like ``0073``. Lines the
previous release creates while it still serves the rollout are not covered
by this migration (it has already run); ``manage.py backfill_line_snapshots
--all-tenants`` after the rollout covers them, and is the same idempotent
function. The columns are NOT NULL with a ``db_default`` from the start, so
nothing needs tightening in a second release.
"""

from django.db import migrations

from order.line_snapshot import backfill_line_snapshots


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0100_order_overcharged_at_and_line_snapshot"),
    ]

    operations = [
        migrations.RunPython(
            lambda apps, schema_editor: backfill_line_snapshots(
                schema_editor.connection
            ),
            migrations.RunPython.noop,
        ),
    ]
