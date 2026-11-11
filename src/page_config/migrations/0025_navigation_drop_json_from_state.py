"""Retire ``NavigationMenu.items`` / ``i18n`` — step one of two.

0024 rebuilt every menu as rows, and ``localized()`` has read rows since
v3.64.0. The JSON fields are now dead weight, but a deploy runs its
migrations in the PreSync hook BEFORE the new pods exist, and the old
replicas keep serving until the rollout completes. Old code selects
these columns by name on every navigation request, so dropping them
here would 500 the storefront chrome for the length of the rollout.

So this migration only takes the fields out of Django's STATE (new code
stops selecting them) after making the columns nullable (new code stops
supplying them on insert). The physical ``DROP COLUMN`` is a later
migration, run once no replica references the columns:

    migrations.RunSQL(
        "ALTER TABLE page_config_navigationmenu "
        "DROP COLUMN items, DROP COLUMN i18n",
        reverse_sql=...,
    )

Plain ``RemoveField`` cannot be used for that step — by then the fields
are gone from state, so Django has nothing to resolve the column from.
"""

from django.db import migrations, models
from django.core.serializers.json import DjangoJSONEncoder


class Migration(migrations.Migration):
    dependencies = [("page_config", "0024_navigation_backfill")]

    operations = [
        migrations.AlterField(
            model_name="navigationmenu",
            name="items",
            field=models.JSONField(
                blank=True,
                null=True,
                default=list,
                encoder=DjangoJSONEncoder,
                verbose_name="Items",
            ),
        ),
        migrations.AlterField(
            model_name="navigationmenu",
            name="i18n",
            field=models.JSONField(
                blank=True,
                null=True,
                default=dict,
                encoder=DjangoJSONEncoder,
                verbose_name="Locale Overrides",
            ),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="navigationmenu", name="items"),
                migrations.RemoveField(model_name="navigationmenu", name="i18n"),
            ],
            database_operations=[],
        ),
    ]
