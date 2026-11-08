"""Allocate the money booked before allocations existed.

Split from the CREATE TABLE migration on purpose, like ``0101``. Every
source that still owes a credit (a refund booked without a claim, a
provider refund attempt, a chargeback decision that the goods came back)
is given its allocation here, once, so no read path ever allocates
(``order.credit_allocation_backfill``). Money the previous release books
while it still serves the rollout has no allocation: the credit
reconciliation reports it on the order instead of crediting it wrongly, and
the function is idempotent, so a re-run allocates it.
"""

from django.db import migrations

from order.credit_allocation_backfill import backfill_credit_allocations


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0102_credit_allocation"),
    ]

    operations = [
        migrations.RunPython(
            lambda apps, schema_editor: backfill_credit_allocations(apps),
            migrations.RunPython.noop,
        ),
    ]
