"""Drop the Leaflet map keys ``0004`` seeded into ACS provider metadata.

``0004_seed_provider_metadata`` stored ``tile_provider``,
``default_map_center`` and ``default_map_zoom`` for the ACS Smartpoint
map, to reach the storefront through the shipping-options metadata.
The storefront never passed them to the map: ``StepShipping`` gives the
locker picker no provider metadata, so the map always centred on the
lockers themselves and used its own tiles. The storefront now owns its
basemap outright — CARTO needs a platform API key since 2026-08, which
the keyless URLs stored here can never carry — so these keys are dead
data, and ``shipping_acs.config.map_config`` that surfaced them goes
with this release.

Nothing reads the keys in the release still serving during the PreSync
rollout either, so they go in one step. Idempotent: popping an absent
key is a no-op. Reverse is a no-op — restoring keys nothing reads would
bring back the watermarked tile URLs for no one.
"""

from __future__ import annotations

from django.db import migrations

MAP_METADATA_KEYS = (
    "tile_provider",
    "default_map_center",
    "default_map_zoom",
)


def drop_map_metadata(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    ShippingProvider = apps.get_model("shipping", "ShippingProvider")

    for provider in ShippingProvider.objects.using(db_alias).filter(
        metadata__has_any_keys=MAP_METADATA_KEYS
    ):
        for key in MAP_METADATA_KEYS:
            provider.metadata.pop(key, None)
        provider.save(update_fields=["metadata"])


class Migration(migrations.Migration):
    dependencies = [
        ("shipping", "0014_drop_legacy_shipping_settings"),
    ]

    operations = [
        migrations.RunPython(drop_map_metadata, migrations.RunPython.noop),
    ]
