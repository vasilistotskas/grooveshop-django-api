"""Retire ``PayWay.configuration`` — step two of two.

0027 (v3.92.0) took the field out of Django's state and left the nullable
column in place, so the replicas still running the previous release kept
writing it through that rollout. v3.92.0 now runs everywhere and no code
references the column, so this drops it. Nothing needs copying first:
payment credentials already live on the tenant.

``RunSQL`` rather than ``RemoveField`` because the field is already gone
from state. The reverse re-creates it nullable, exactly the shape 0027
left it in, so rolling back this migration alone is well-defined.
"""

from django.db import migrations


class Migration(migrations.Migration):
    contract_of = [("pay_way", "0027_payway_configuration_drop_from_state")]

    dependencies = [("pay_way", "0030_payway_translation_drop_name_column")]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE pay_way_payway DROP COLUMN IF EXISTS configuration",
            reverse_sql=(
                "ALTER TABLE pay_way_payway ADD COLUMN configuration jsonb NULL"
            ),
        ),
    ]
