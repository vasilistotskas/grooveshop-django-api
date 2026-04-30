"""Seed structural per-provider config into ``ShippingProvider.metadata``.

Phase 0 of the ACS Smartpoint map work: pull every magic value
(``_LOCKER_KINDS``, the hardcoded ``[:20]`` nearest limit, the default
country fallback, weight bounds, default map centre/zoom) out of the
source code and into the existing ``metadata`` JSONField. Once this
runs, adding a new courier or onboarding a new country becomes one
admin row update, not a code change.

Idempotent: re-runs merge new keys on top of whatever's already there
without clobbering operator-set overrides. The tests in
``tests/unit/shipping_acs/test_metadata_driven_config.py`` assert that
overriding any of these keys changes effective behaviour without code
edits — guard against the regressions this migration is meant to
prevent.
"""

from __future__ import annotations

from django.db import migrations

# Kept here (not imported from ``shipping_acs.enum``) so the migration
# stays self-contained — Django migrations should never import from app
# code that may move or be deleted in a later refactor.
_ACS_SHOP_KINDS_BY_COUNTRY = {
    "GR": [7, 8],  # Smartpoint inbound + outbound (Greek catalogue)
    "CY": [7],  # Cyprus catalogue lists 7 only (per ACS PDF)
}

_ACS_DEFAULT_METADATA = {
    "shop_kinds_by_country": _ACS_SHOP_KINDS_BY_COUNTRY,
    "nearest_limit": 20,
    "min_weight_kg": "0.5",
    "max_weight_kg": "999",
    "default_voucher_language": "GR",
    "default_map_center": [37.9838, 23.7275],  # Athens
    "default_map_zoom": 11,
    "tile_provider": {
        "light": {
            "url": (
                "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
            ),
            "attribution": (
                '&copy; <a href="https://www.openstreetmap.org/copyright">'
                "OpenStreetMap</a> contributors &copy; "
                '<a href="https://carto.com/attributions">CARTO</a>'
            ),
            "max_zoom": 19,
            "subdomains": "abcd",
        },
        "dark": {
            "url": (
                "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
            ),
            "attribution": (
                '&copy; <a href="https://www.openstreetmap.org/copyright">'
                "OpenStreetMap</a> contributors &copy; "
                '<a href="https://carto.com/attributions">CARTO</a>'
            ),
            "max_zoom": 19,
            "subdomains": "abcd",
        },
    },
}

_BOXNOW_DEFAULT_METADATA = {
    # BoxNow uses an iframe widget; no Leaflet config needed. We seed
    # the locker-picker kind hint so the frontend dispatch table can
    # decide ``usesGenericPicker`` from a single source.
    "uses_generic_picker": False,
}


def _merge_metadata(existing: dict, defaults: dict) -> dict:
    """Deep-merge ``defaults`` under ``existing`` — operator wins.

    We don't ``existing.update(defaults)`` because that would let the
    migration overwrite admin-tuned values on re-run. Instead, only
    fill in missing keys at the top level. Nested dicts (like
    ``tile_provider``) are left untouched once present so an operator
    can swap a tile URL without us reverting it.
    """
    merged = dict(existing or {})
    for key, value in defaults.items():
        merged.setdefault(key, value)
    return merged


def seed_metadata(apps, _schema_editor):
    ShippingProvider = apps.get_model("shipping", "ShippingProvider")

    for code, defaults in (
        ("acs", _ACS_DEFAULT_METADATA),
        ("boxnow", _BOXNOW_DEFAULT_METADATA),
    ):
        provider = ShippingProvider.objects.filter(code=code).first()
        if provider is None:
            # Earlier seed migration didn't run (fresh-on-fresh flake);
            # nothing to update — the next run after 0002 lands will
            # backfill correctly.
            continue
        provider.metadata = _merge_metadata(provider.metadata, defaults)
        provider.save(update_fields=["metadata"])


def unseed_metadata(apps, _schema_editor):
    """Remove the keys we added — leave any operator-added keys alone."""
    ShippingProvider = apps.get_model("shipping", "ShippingProvider")

    keys_per_code = {
        "acs": tuple(_ACS_DEFAULT_METADATA.keys()),
        "boxnow": tuple(_BOXNOW_DEFAULT_METADATA.keys()),
    }
    for code, keys in keys_per_code.items():
        provider = ShippingProvider.objects.filter(code=code).first()
        if provider is None:
            continue
        metadata = dict(provider.metadata or {})
        for key in keys:
            metadata.pop(key, None)
        provider.metadata = metadata
        provider.save(update_fields=["metadata"])


class Migration(migrations.Migration):
    dependencies = [
        ("shipping", "0003_acs_supports_pickup_point"),
    ]

    operations = [
        migrations.RunPython(seed_metadata, reverse_code=unseed_metadata),
    ]
