"""Retire the pay way's ``settlement`` mirrors — step two of two.

0034 (release C, running in production since 2026-10-22) took
``is_online_payment``, ``requires_confirmation`` and their index
``pay_way_online_ix`` out of Django's state, leaving the columns with a
database default so the release before it could keep writing them through
that rollout. That release is gone and no code references them, so this
drops the index and both columns.

``RunSQL`` rather than ``RemoveField``/``RemoveIndex`` because they are
already gone from state. The reverse re-creates them in the shape 0034 left
them and refills them from ``settlement``, which they only ever mirrored.
"""

from django.db import migrations


class Migration(migrations.Migration):
    contract_of = [("pay_way", "0034_payway_online_mirrors_out_of_state")]

    dependencies = [("pay_way", "0034_payway_online_mirrors_out_of_state")]

    operations = [
        migrations.RunSQL(
            sql=(
                "DROP INDEX IF EXISTS pay_way_online_ix; "
                "ALTER TABLE pay_way_payway "
                "DROP COLUMN IF EXISTS is_online_payment, "
                "DROP COLUMN IF EXISTS requires_confirmation"
            ),
            reverse_sql=(
                "ALTER TABLE pay_way_payway "
                "ADD COLUMN IF NOT EXISTS is_online_payment boolean "
                "NOT NULL DEFAULT false, "
                "ADD COLUMN IF NOT EXISTS requires_confirmation boolean "
                "NOT NULL DEFAULT false; "
                "UPDATE pay_way_payway SET "
                "is_online_payment = settlement = 'online', "
                "requires_confirmation = settlement = 'offline_transfer'; "
                "CREATE INDEX IF NOT EXISTS pay_way_online_ix "
                "ON pay_way_payway (is_online_payment)"
            ),
        ),
    ]
