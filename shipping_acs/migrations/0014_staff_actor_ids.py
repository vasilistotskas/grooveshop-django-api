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
        ('shipping_acs', '0013_acs_cod_payout_unreadable_rows_and_reconciliation'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
            migrations.RemoveField(
                model_name='acscodpayout',
                name='reconciled_by',
            ),
            migrations.RemoveField(
                model_name='acspickuplist',
                name='issued_by',
            ),
            migrations.RemoveField(
                model_name='acsshipment',
                name='cod_received_outside_acs_by',
            ),
            migrations.RemoveField(
                model_name='historicalacsshipment',
                name='cod_received_outside_acs_by',
            ),
            migrations.RemoveField(
                model_name='historicalacsshipment',
                name='history_user',
            ),
            migrations.AddField(
                model_name='acscodpayout',
                name='reconciled_by_id',
                field=models.BigIntegerField(blank=True, help_text='Operator who reconciled the payout. Id of a platform staff account (public schema), not a store user. Read it through the matching accessor.', null=True, verbose_name='Reconciled by'),
            ),
            migrations.AddField(
                model_name='acspickuplist',
                name='issued_by_id',
                field=models.BigIntegerField(blank=True, help_text='Admin who triggered the manual issue; empty when the daily Celery beat task issued the list. Id of a platform staff account (public schema), not a store user. Read it through the matching accessor.', null=True, verbose_name='Issued by'),
            ),
            migrations.AddField(
                model_name='acsshipment',
                name='cod_received_outside_acs_by_id',
                field=models.BigIntegerField(blank=True, help_text='Operator who recorded the settlement. Id of a platform staff account (public schema), not a store user. Read it through the matching accessor.', null=True, verbose_name='COD received outside ACS by'),
            ),
            migrations.AddField(
                model_name='historicalacsshipment',
                name='cod_received_outside_acs_by_id',
                field=models.BigIntegerField(blank=True, help_text='Operator who recorded the settlement. Id of a platform staff account (public schema), not a store user. Read it through the matching accessor.', null=True, verbose_name='COD received outside ACS by'),
            ),
            migrations.AddField(
                model_name='historicalacsshipment',
                name='history_user_id',
                field=models.BigIntegerField(null=True),
            ),
            ],
        ),
    ]
