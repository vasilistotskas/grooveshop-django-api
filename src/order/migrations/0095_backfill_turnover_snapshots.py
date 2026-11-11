"""Give every existing order its VAT lines and its ship moment.

``OrderVatLine`` and ``Order.shipped_at`` (0094) are written from now on
(``order.vat_snapshot``). Orders that predate them are filled from what
they already hold: the lines from the same ``order_vat_lines`` their
receipt is built by, the moment from the order's history.

Split from the schema migration on purpose, like ``0065``. Orders the
previous release creates or ships while it still serves the rollout are
not covered by this migration; ``manage.py backfill_turnover_snapshots``
after the rollout covers them, and is the same idempotent functions.
"""

from django.db import migrations

from order.vat_snapshot import backfill_shipped_at, backfill_vat_lines


def backfill(apps, schema_editor):
    backfill_vat_lines(apps)
    backfill_shipped_at(apps)


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0094_order_shipped_at_ordervatline"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
