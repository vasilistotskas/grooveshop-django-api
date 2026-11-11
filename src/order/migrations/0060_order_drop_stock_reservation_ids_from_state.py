"""Retire ``Order.stock_reservation_ids`` — step one of two.

The column was never written: the order services record the converted
reservations in ``metadata["stock_reservation_ids"]`` and read them back
from there. A deploy runs its migrations in the PreSync hook BEFORE the
new pods exist, and the old replicas select this column by name on
every order query, so dropping it here would fail them for the length
of the rollout.

So this migration only makes the column nullable (new code stops
supplying it on insert) and takes the field out of Django's STATE. The
physical ``DROP COLUMN`` is a later migration, run once no replica
references the column:

    migrations.RunSQL(
        "ALTER TABLE order_order DROP COLUMN stock_reservation_ids",
        reverse_sql=...,
    )
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("order", "0059_orderattribution")]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="stock_reservation_ids",
            field=models.JSONField(
                blank=True,
                null=True,
                default=list,
                verbose_name="Stock Reservation IDs",
            ),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="order", name="stock_reservation_ids"
                ),
            ],
            database_operations=[],
        ),
    ]
