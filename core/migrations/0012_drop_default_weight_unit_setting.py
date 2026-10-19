"""Drop the ``DEFAULT_WEIGHT_UNIT`` setting no code reads.

A product's weight carries its own unit (``MeasurementField``), so the
seeded default never decided anything; an operator who changed it saw no
effect. Only deletes the row, so the release still serving during the
PreSync rollout (which never reads it) is unaffected.

Reverse is a no-op: restoring it would recreate a setting that does
nothing.
"""

from django.db import migrations

UNUSED_SETTINGS = ("DEFAULT_WEIGHT_UNIT",)


def drop_unused_settings(apps, schema_editor):
    Setting = apps.get_model("extra_settings", "Setting")
    Setting.objects.using(schema_editor.connection.alias).filter(
        name__in=UNUSED_SETTINGS
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0011_drop_unused_settings"),
        ("extra_settings", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(drop_unused_settings, migrations.RunPython.noop),
    ]
