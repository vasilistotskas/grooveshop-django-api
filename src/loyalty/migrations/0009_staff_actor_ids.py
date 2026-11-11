"""Staff members named on store rows are plain ids, not a relation.

Release N (expand), state only: the columns keep their name, type and data
(``x_id`` bigint NULL); only Django's idea of them changes, from a foreign
key to the store's user table (which names a shopper who shares the id) to
an id read through ``tenant.staff_actors``. No database operation, so the
release still serving reads the same columns.
"""

from django.db import migrations, models

from tenant.migration_helpers import drop_actor_fk


class Migration(migrations.Migration):

    dependencies = [
        ('loyalty', '0008_points_restore_per_refund'),
    ]

    operations = [
        drop_actor_fk("loyalty_points_transaction", "created_by_id"),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
            migrations.RemoveField(
                model_name='pointstransaction',
                name='created_by',
            ),
            migrations.AddField(
                model_name='pointstransaction',
                name='created_by_id',
                field=models.BigIntegerField(blank=True, help_text='Admin who created this adjustment. Id of a platform staff account (public schema), not a store user. Read it through the matching accessor.', null=True, verbose_name='Created By'),
            ),
            ],
        ),
    ]
