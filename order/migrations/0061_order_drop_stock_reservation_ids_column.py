"""Retire ``Order.stock_reservation_ids`` — step two of two.

0060 (v3.91.0) made the column nullable and took the field out of
Django's state, so the replicas still running the previous release kept
selecting it through that rollout. v3.91.0 now runs everywhere and no
code references the column — it was never written, the order services
keep the ids in ``metadata["stock_reservation_ids"]`` — so this drops it.
Nothing needs copying first.

``RunSQL`` rather than ``RemoveField`` because the field is already gone
from state. The reverse re-creates it nullable, exactly the shape 0060
left it in, so rolling back this migration alone is well-defined.
"""

from django.db import migrations


class Migration(migrations.Migration):
    contract_of = [
        ("order", "0060_order_drop_stock_reservation_ids_from_state")
    ]

    dependencies = [
        ("order", "0060_order_drop_stock_reservation_ids_from_state")
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE order_order "
                "DROP COLUMN IF EXISTS stock_reservation_ids"
            ),
            reverse_sql=(
                "ALTER TABLE order_order "
                "ADD COLUMN stock_reservation_ids jsonb NULL"
            ),
        ),
    ]
