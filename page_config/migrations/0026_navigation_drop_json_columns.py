"""Retire ``NavigationMenu.items`` / ``i18n`` — step two of two.

0025 removed the fields from Django's state and left the columns in
place so the replicas still running the previous release kept selecting
them through the rollout. That release is now fully deployed and nothing
references the columns, so this drops them.

``RunSQL`` rather than ``RemoveField`` because the fields are already
gone from state — Django has nothing to resolve the column from. The
reverse re-creates them nullable, which is exactly the shape 0025 left
them in, so a rollback of this migration alone is well-defined.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("page_config", "0025_navigation_drop_json_from_state")]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE page_config_navigationmenu "
                "DROP COLUMN IF EXISTS items, DROP COLUMN IF EXISTS i18n"
            ),
            reverse_sql=(
                "ALTER TABLE page_config_navigationmenu "
                "ADD COLUMN items jsonb NULL, ADD COLUMN i18n jsonb NULL"
            ),
        ),
    ]
