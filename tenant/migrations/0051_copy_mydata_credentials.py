"""Copy each store's myDATA credentials onto its ``Tenant`` row.

``MYDATA_USER_ID`` and ``MYDATA_SUBSCRIPTION_KEY`` were per-schema
``extra_settings.Setting`` rows; they are now ``Tenant.mydata_user_id``
and the encrypted ``Tenant.mydata_subscription_key``, read through
``tenant.credentials.mydata_credentials``.

``tenant`` is a SHARED_APPS app, so this runs once, in the public
schema, and reaches each store's ``extra_settings_setting`` table with a
schema-qualified query. A store whose schema has no such table (not
provisioned yet) is skipped. Only a still-empty Tenant column is filled,
so a re-run never overwrites what an operator typed since.

The Setting rows are NOT deleted here: the release still serving during
the rollout reads them. ``0052_drop_plaintext_tenant_secrets`` copies
what that release wrote meanwhile and deletes them.
"""

from django.db import migrations
from psycopg import sql

MYDATA_SETTINGS = {
    "MYDATA_USER_ID": "mydata_user_id",
    "MYDATA_SUBSCRIPTION_KEY": "mydata_subscription_key",
}


def copy_mydata_credentials(apps, schema_editor):
    Tenant = apps.get_model("tenant", "Tenant")
    with schema_editor.connection.cursor() as cursor:
        for tenant in Tenant.objects.all():
            cursor.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = %s "
                "AND table_name = 'extra_settings_setting'",
                [tenant.schema_name],
            )
            if cursor.fetchone() is None:
                continue
            filled = []
            for setting, field in MYDATA_SETTINGS.items():
                if getattr(tenant, field):
                    continue
                cursor.execute(
                    sql.SQL(
                        "SELECT value_string FROM {}.extra_settings_setting "
                        "WHERE name = %s"
                    ).format(sql.Identifier(tenant.schema_name)),
                    [setting],
                )
                row = cursor.fetchone()
                if row and row[0]:
                    setattr(tenant, field, row[0])
                    filled.append(field)
            if filled:
                tenant.save(update_fields=filled)


class Migration(migrations.Migration):
    dependencies = [
        ("tenant", "0050_encrypt_tenant_secrets"),
    ]

    operations = [
        migrations.RunPython(
            copy_mydata_credentials, migrations.RunPython.noop
        ),
    ]
