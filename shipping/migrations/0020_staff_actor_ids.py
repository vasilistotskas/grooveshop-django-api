"""Staff members named on store rows are plain ids, not a relation.

Release N (expand), state only: the columns keep their name, type and data
(``x_id`` bigint NULL); only Django's idea of them changes, from a foreign
key to the store's user table (which names a shopper who shares the id) to
an id read through ``tenant.staff_actors``. No database operation, so the
release still serving reads the same columns.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shipping', '0019_free_threshold_help'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
            migrations.RemoveField(
                model_name='historicalshippingrate',
                name='history_user',
            ),
            migrations.AddField(
                model_name='historicalshippingrate',
                name='history_user_id',
                field=models.BigIntegerField(null=True),
            ),
            ],
        ),
    ]
