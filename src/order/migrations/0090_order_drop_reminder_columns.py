"""Retire the pending-order reminder counters — step two of two.

0084 (shipped long ago, running in production) took ``Order.reminder_count``
and ``last_reminder_sent_at`` out of Django's state, leaving the columns in
the database so the replicas still serving the release before it kept
selecting and inserting them through that rollout. That release is gone, no
code references the columns, and nothing reads the counters (the reminder
task was removed with 0084), so this drops them.

Nothing is re-copied: the counters only fed the reminder task, so no later
code needs what the old replicas wrote.

``RunSQL`` rather than ``RemoveField`` because the fields are already gone
from state. The reverse re-creates both columns in the shape 0084 left them
(``reminder_count`` NOT NULL with its database default), without values.
"""

from django.db import migrations


class Migration(migrations.Migration):
    contract_of = [("order", "0084_drop_pending_order_reminders")]

    dependencies = [("order", "0089_remove_german_translations")]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE order_order "
                "DROP COLUMN IF EXISTS reminder_count, "
                "DROP COLUMN IF EXISTS last_reminder_sent_at"
            ),
            reverse_sql=(
                "ALTER TABLE order_order "
                "ADD COLUMN IF NOT EXISTS reminder_count smallint "
                "NOT NULL DEFAULT 0 CHECK (reminder_count >= 0), "
                "ADD COLUMN IF NOT EXISTS last_reminder_sent_at "
                "timestamp with time zone NULL"
            ),
        ),
    ]
