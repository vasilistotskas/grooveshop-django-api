"""Retire ``PayWayTranslation.name`` — step two of two.

0029 (v3.99.0) moved the language-independent key onto ``PayWay.key`` and
took the translated ``name`` out of Django's state, leaving the nullable
column in place so the replicas still running the previous release kept
reading it through that rollout. v3.99.0 now runs everywhere and no code
references the column, so this drops it. Nothing needs copying first:
0029 already carried every name onto ``PayWay.key``.

``RunSQL`` rather than ``RemoveField`` because the field is already gone
from state. The reverse re-creates it nullable, exactly the shape 0029
left it in, so rolling back this migration alone is well-defined.
"""

from django.db import migrations


class Migration(migrations.Migration):
    contract_of = [("pay_way", "0029_payway_key")]

    dependencies = [("pay_way", "0029_payway_key")]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE pay_way_payway_translation "
                "DROP COLUMN IF EXISTS name"
            ),
            reverse_sql=(
                "ALTER TABLE pay_way_payway_translation "
                "ADD COLUMN name varchar(50) NULL"
            ),
        ),
    ]
