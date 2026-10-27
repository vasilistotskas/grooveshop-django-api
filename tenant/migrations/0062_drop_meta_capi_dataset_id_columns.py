"""Retire ``Tenant.meta_capi_dataset_id`` — step two of two.

0061 (in production since 2026-10-27) took the field out of Django's state
on the tenant and its history, leaving both columns with a database default
so the release before it could keep writing them through that rollout.
That release is gone and no code references them, so this drops both.

``RunSQL`` rather than ``RemoveField`` because they are already gone from
state. Nothing ever read the value (the Conversions API posts to
``meta_pixel_id``), so the reverse re-creates the columns empty, in the
shape 0061 left them.
"""

from django.db import migrations

TABLES = ("tenant_tenant", "tenant_historicaltenant")


class Migration(migrations.Migration):
    contract_of = [("tenant", "0061_meta_capi_dataset_id_out_of_state")]

    dependencies = [("tenant", "0061_meta_capi_dataset_id_out_of_state")]

    operations = [
        migrations.RunSQL(
            sql=" ".join(
                f"ALTER TABLE IF EXISTS {table} "
                "DROP COLUMN IF EXISTS meta_capi_dataset_id;"
                for table in TABLES
            ),
            reverse_sql=" ".join(
                f"ALTER TABLE IF EXISTS {table} "
                "ADD COLUMN IF NOT EXISTS meta_capi_dataset_id "
                "varchar(64) NOT NULL DEFAULT '';"
                for table in TABLES
            ),
        ),
    ]
