"""Delete the ``OPS_ALERT_<KIND>_ENABLED`` store setting rows: step two.

0003 moved every muted alert into ``EmailSwitch`` and, in that release,
stopped the code reading the settings; it kept the rows because the
previous image still read them during the rollout. That image is gone and
nothing declares or reads the settings, so the leftover rows (a store only
has one once a staff member saved the settings form) are removed here, in
every store's schema and in public.

The delete is not reversed: the mute each row carried already lives in its
``EmailSwitch`` row, so there is nothing to put back.
"""

from django.db import migrations

PREFIX = "OPS_ALERT_"
SUFFIX = "_ENABLED"


def delete_settings(apps, schema_editor):
    Setting = apps.get_model("extra_settings", "Setting")
    Setting.objects.filter(
        name__startswith=PREFIX, name__endswith=SUFFIX
    ).delete()


class Migration(migrations.Migration):
    contract_of = [("emails", "0003_import_ops_alert_switches")]

    dependencies = [
        ("emails", "0003_import_ops_alert_switches"),
        ("extra_settings", "0008_auto_20221024_0717"),
    ]

    operations = [
        migrations.RunPython(delete_settings, migrations.RunPython.noop),
    ]
