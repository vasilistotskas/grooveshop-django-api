"""Retire ``Order.paid_amount`` — step two of two.

0070 (v3.106.2) added ``Order.amount_due`` and took ``paid_amount`` and its
currency column out of Django's state, leaving both nullable in the
database so the replicas still running the previous release kept selecting
and inserting them through that rollout. v3.106.2 now runs everywhere, the
storefront and the agent gateway read ``amountDue``, and no code references
the columns, so this drops them.

An order the previous release created while it still served wrote
``paid_amount`` and left ``amount_due`` at its database default (0): its
INSERT did not name the new column. The statement below re-copies exactly
those rows (``amount_due = 0 AND paid_amount > 0``) before the columns go,
so none of them reads 0 owed. A row the new code wrote already has the
right ``amount_due`` and is never overwritten.

``RunSQL`` rather than ``RemoveField`` because the fields are already gone
from state. The reverse re-creates both columns nullable, exactly the shape
0070 left them in, so rolling back this migration alone is well-defined.
The values are not restored: nothing at v3.106.2 reads them.
"""

from django.db import migrations


class Migration(migrations.Migration):
    contract_of = [("order", "0070_order_amount_due")]

    dependencies = [("order", "0070_order_amount_due")]

    operations = [
        migrations.RunSQL(
            sql=(
                "UPDATE order_order "
                "SET amount_due = paid_amount, "
                "amount_due_currency = COALESCE(paid_amount_currency, 'EUR') "
                "WHERE amount_due = 0 AND paid_amount > 0; "
                "ALTER TABLE order_order "
                "DROP COLUMN IF EXISTS paid_amount, "
                "DROP COLUMN IF EXISTS paid_amount_currency"
            ),
            reverse_sql=(
                "ALTER TABLE order_order "
                "ADD COLUMN IF NOT EXISTS paid_amount numeric(11, 2) NULL, "
                "ADD COLUMN IF NOT EXISTS paid_amount_currency varchar(3) NULL"
            ),
        ),
    ]
