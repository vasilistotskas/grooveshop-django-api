"""Make ``EmailSwitch`` the one record of which store emails a store muted.

Before the operational alerts became email types, a store muted one with
its own ``OPS_ALERT_<KIND>_ENABLED`` store setting. The send pipeline reads
``EmailSwitch`` alone, so every setting a store turned OFF becomes the
``ops.<kind>`` switch row here. A setting still on needs no row (the table
is sparse: no row means on). Additive and idempotent: an existing row is
left as it is. Runs once per schema (``emails`` and ``extra_settings`` are
in both the public and every store's schema).

The setting rows are not deleted here: the previous image keeps reading
them while this release rolls out, so they go in a later release
(``contract_of`` this migration). Nothing reads them from this release on.
"""

from django.db import migrations

PREFIX = "OPS_ALERT_"
SUFFIX = "_ENABLED"
TYPE_PREFIX = "ops."


def type_key(setting_name: str) -> str:
    """``OPS_ALERT_STALE_SHIPMENTS_ENABLED`` -> ``ops.stale_shipments``."""
    kind = setting_name.removeprefix(PREFIX).removesuffix(SUFFIX)
    return f"{TYPE_PREFIX}{kind.lower()}"


def import_switches(apps, schema_editor):
    from tenant.cache import bump_cache_generation

    Setting = apps.get_model("extra_settings", "Setting")
    EmailSwitch = apps.get_model("emails", "EmailSwitch")

    muted = Setting.objects.filter(
        name__startswith=PREFIX, name__endswith=SUFFIX, value_bool=False
    ).values_list("name", flat=True)
    created = EmailSwitch.objects.bulk_create(
        [EmailSwitch(type_key=type_key(name)) for name in muted],
        ignore_conflicts=True,
    )
    schema = schema_editor.connection.schema_name
    if created and schema != "public":
        # The pipeline caches a store's switches per cache generation; a
        # bulk insert fires no signal, so move it on here.
        bump_cache_generation(schema_name=schema)


class Migration(migrations.Migration):
    dependencies = [
        ("emails", "0002_import_marketing_opt_outs"),
        ("extra_settings", "0008_auto_20221024_0717"),
    ]

    operations = [
        migrations.RunPython(import_switches, migrations.RunPython.noop),
    ]
