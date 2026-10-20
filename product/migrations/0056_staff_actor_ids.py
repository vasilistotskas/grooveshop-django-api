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
        ('product', '0055_favourites_setting_description'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
            migrations.RemoveField(
                model_name='historicalproduct',
                name='changed_by',
            ),
            migrations.RemoveField(
                model_name='historicalproduct',
                name='history_user',
            ),
            migrations.RemoveField(
                model_name='product',
                name='changed_by',
            ),
            migrations.AddField(
                model_name='historicalproduct',
                name='changed_by_id',
                field=models.BigIntegerField(blank=True, help_text='Staff member who last changed the product from the admin. Id of a platform staff account (public schema), not a store user. Read it through the matching accessor.', null=True, verbose_name='Changed By'),
            ),
            migrations.AddField(
                model_name='historicalproduct',
                name='history_user_id',
                field=models.BigIntegerField(null=True),
            ),
            migrations.AddField(
                model_name='product',
                name='changed_by_id',
                field=models.BigIntegerField(blank=True, help_text='Staff member who last changed the product from the admin. Id of a platform staff account (public schema), not a store user. Read it through the matching accessor.', null=True, verbose_name='Changed By'),
            ),
            ],
        ),
    ]
