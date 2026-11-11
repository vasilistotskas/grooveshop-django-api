"""Encrypt the tenant secrets at rest: contract step (release N+1).

Ships only once release N (``0050``, ``0051``) is what production runs.
``0050`` moved every ``Tenant`` secret onto a ``<name>_encrypted`` column
and left the old plaintext columns in place for the release still
serving during its rollout. This drops them, and deletes the retired
``MYDATA_USER_ID`` / ``MYDATA_SUBSCRIPTION_KEY`` ``extra_settings`` rows
that ``0051`` copied.

First it carries over whatever the previous release wrote while it still
served, using the rule the expand step used: a value is copied only into
a column that is still empty, so nothing set since through the new code
is ever overwritten. One case cannot be told apart: a secret the new
release CLEARED after the rollout still sits in its old column, and is
copied back. A store that cleared a secret on purpose clears it again.

The rollback target after this migration is release N, not N-1: N-1
still reads the dropped columns.
"""

from importlib import import_module

from django.db import migrations
from psycopg import sql

expand = import_module("tenant.migrations.0050_encrypt_tenant_secrets")
mydata = import_module("tenant.migrations.0051_copy_mydata_credentials")

# The varchar length each old column had, for the reverse migration.
OLD_COLUMN_LENGTHS = {
    "acs_company_password": 128,
    "acs_user_password": 128,
}
DEFAULT_LENGTH = 255

DROP_SQL = "\n".join(
    f"ALTER TABLE tenant_tenant DROP COLUMN IF EXISTS {name};"
    for name in expand.SECRET_FIELDS
)
RESTORE_SQL = "\n".join(
    f"ALTER TABLE tenant_tenant ADD COLUMN IF NOT EXISTS {name} "
    f"varchar({OLD_COLUMN_LENGTHS.get(name, DEFAULT_LENGTH)}) "
    "NOT NULL DEFAULT '';"
    for name in expand.SECRET_FIELDS
)


def carry_over_late_writes(apps, schema_editor):
    """Fill what the previous release wrote, then retire the settings."""
    expand.encrypt_existing_secrets(apps, schema_editor)
    mydata.copy_mydata_credentials(apps, schema_editor)

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
            cursor.execute(
                sql.SQL(
                    "DELETE FROM {}.extra_settings_setting "
                    "WHERE name = ANY(%s)"
                ).format(sql.Identifier(tenant.schema_name)),
                [list(mydata.MYDATA_SETTINGS)],
            )


class Migration(migrations.Migration):
    contract_of = [
        ("tenant", "0050_encrypt_tenant_secrets"),
        ("tenant", "0051_copy_mydata_credentials"),
    ]

    dependencies = [
        ("tenant", "0051_copy_mydata_credentials"),
    ]

    operations = [
        migrations.RunPython(carry_over_late_writes, migrations.RunPython.noop),
        migrations.RunSQL(DROP_SQL, reverse_sql=RESTORE_SQL),
    ]
