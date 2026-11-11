"""Drop two settings no code reads.

``LOW_STOCK_THRESHOLD`` and ``NOTIFICATION_EXPIRATION_DAYS`` were seeded
but never read: the low-stock bands are fixed in the dashboard query and
the product admin, and a notification's expiry is set by whoever creates
it. A switch in the admin Settings list that does nothing misleads the
operator. Only deletes rows, so the release still serving during the
PreSync rollout (which never reads them) is unaffected.

Reverse is a no-op: restoring them would recreate settings that do
nothing.
"""

from django.db import migrations

UNUSED_SETTINGS = ("LOW_STOCK_THRESHOLD", "NOTIFICATION_EXPIRATION_DAYS")


def drop_unused_settings(apps, schema_editor):
    Setting = apps.get_model("extra_settings", "Setting")
    Setting.objects.using(schema_editor.connection.alias).filter(
        name__in=UNUSED_SETTINGS
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_cachepurgelog_purge_permission"),
        ("extra_settings", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(drop_unused_settings, migrations.RunPython.noop),
    ]
