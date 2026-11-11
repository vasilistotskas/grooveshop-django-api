"""Expand: ``PayWay.configuration`` leaves the model state.

Nothing read it any more (payment credentials live on the tenant), so
the code stops using it in this release. The column is already
nullable, so the release that still writes it and this one's INSERTs
both work while they overlap. The next release drops it with a
``RunSQL`` migration declaring ``contract_of`` this one.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("pay_way", "0026_payway_exclusion_country"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="payway", name="configuration"),
            ],
            database_operations=[],
        ),
    ]
