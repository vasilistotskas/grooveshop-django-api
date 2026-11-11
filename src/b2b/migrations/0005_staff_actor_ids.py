"""Staff members named on store rows are plain ids, not a relation.

Release N (expand), state only for the column type: the columns keep their name, type and data
(``x_id`` bigint NULL); only Django's idea of them changes, from a foreign
key to the store's user table (which names a shopper who shares the id) to
an id read through ``tenant.staff_actors``. No database operation, so the
release still serving reads the same columns.
"""

from django.db import migrations, models

from tenant.migration_helpers import drop_actor_fk


class Migration(migrations.Migration):

    dependencies = [
        ('b2b', '0004_field_verbose_names'),
    ]

    operations = [
        drop_actor_fk("b2b_business_profile", "reviewed_by_id"),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
            migrations.RemoveField(
                model_name='businessprofile',
                name='reviewed_by',
            ),
            migrations.AddField(
                model_name='businessprofile',
                name='reviewed_by_id',
                field=models.BigIntegerField(blank=True, help_text='Staff member who reviewed the company. Id of a platform staff account (public schema), not a store user. Read it through the matching accessor.', null=True, verbose_name='Reviewed by'),
            ),
            ],
        ),
    ]
