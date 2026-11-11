"""Delete the ``ACS_SMARTPOINT_ENABLED`` ``extra_settings`` rows.

ACS locker pickup no longer has a switch of its own: ``AcsCarrier
.is_kind_enabled`` offers it once active ``AcsStation`` rows exist for the
delivery country (the nightly ``sync_acs_stations`` fills them), on top of
``ShippingProvider.supports_pickup_point`` and an active pickup-point rate.
Nothing reads the setting any more and ``sync_setting_metadata`` only
visits declared names, so a left-behind row would sit in every store's
Settings admin as a switch that does nothing.

Only deletes rows (data, not schema), and a release later than the one
that stopped reading them (django v3.148.0): the release still serving
during the PreSync rollout no longer reads the setting, so deleting a
store's row hides nothing.

Reverse is a no-op: restoring the row would recreate a setting that does
nothing.
"""

from __future__ import annotations

from django.db import migrations


def drop_acs_smartpoint_setting(apps, schema_editor):
    Setting = apps.get_model("extra_settings", "Setting")
    Setting.objects.using(schema_editor.connection.alias).filter(
        name="ACS_SMARTPOINT_ENABLED"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("shipping", "0020_staff_actor_ids"),
        ("extra_settings", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            drop_acs_smartpoint_setting, migrations.RunPython.noop
        ),
    ]
