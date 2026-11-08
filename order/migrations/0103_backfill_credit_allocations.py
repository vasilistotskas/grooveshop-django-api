"""Give the credits issued so far the allocation they were issued from.

Split from the CREATE TABLE migration on purpose, like ``0101``: credits the
previous release issues while it still serves the rollout are covered by the
lazy rule (``order.credit_allocation.ensure_allocations`` counts a credit
without an allocation against the headroom), and
``order.credit_allocation_backfill`` is idempotent.
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
