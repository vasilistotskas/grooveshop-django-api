"""Retire the Tenant theme fields, step two of two.

0072 (in production since 2026-11-06) took the fields out of Django's state on
the tenant and its history, leaving both tables' columns with a database
default so the release before it could keep writing them through that rollout.
That release is gone and no code references them, so this drops them.

``RunSQL`` rather than ``RemoveField`` because they are already gone from
state. Nothing reads the values (a store's look is its theme document), so the
reverse re-creates the columns with their defaults, in the shape 0072 left them.
"""

from django.db import migrations

TABLES = ("tenant_tenant", "tenant_historicaltenant")

COLUMNS = {
    "primary_color": "varchar(20) NOT NULL DEFAULT 'neutral'",
    "neutral_color": "varchar(20) NOT NULL DEFAULT 'zinc'",
    "accent_hex": "varchar(7) NOT NULL DEFAULT '#003DFF'",
    "success_hex": "varchar(7) NOT NULL DEFAULT '#16a34a'",
    "warning_hex": "varchar(7) NOT NULL DEFAULT '#ca8a04'",
    "error_hex": "varchar(7) NOT NULL DEFAULT '#dc2626'",
    "info_hex": "varchar(7) NOT NULL DEFAULT '#2563eb'",
    "theme_preset": "varchar(20) NOT NULL DEFAULT 'default'",
    "theme_metadata": "jsonb NOT NULL DEFAULT '{}'",
}


class Migration(migrations.Migration):
    contract_of = [("tenant", "0072_theme_fields_out_of_state")]

    dependencies = [("tenant", "0072_theme_fields_out_of_state")]

    operations = [
        migrations.RunSQL(
            sql=" ".join(
                f"ALTER TABLE IF EXISTS {table} DROP COLUMN IF EXISTS {column};"
                for table in TABLES
                for column in COLUMNS
            ),
            reverse_sql=" ".join(
                f"ALTER TABLE IF EXISTS {table} "
                f"ADD COLUMN IF NOT EXISTS {column} {definition};"
                for table in TABLES
                for column, definition in COLUMNS.items()
            ),
        ),
    ]
