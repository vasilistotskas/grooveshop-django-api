"""Move the location map's coordinates to the store's own location.

A store's location was held twice: the ``STORE_GEO_LAT`` /
``STORE_GEO_LNG`` settings, which the storefront's LocalBusiness
structured data reads, and ``lat`` / ``lng`` on each ``location_map``
section, which drew the map. Nothing kept them equal, and they drifted:
on 2026-10-15 one store's map showed the owner's pin while the data it
gave search engines pointed about 750 m away. The section now draws the
store's own location (like ``business_hours`` reads ``BUSINESS_HOURS``),
so its contract loses both keys.

For each ``location_map`` section, in ``pk`` order: when it carries a
full pair of coordinates and the store has no location yet, the pair
becomes the store's location; then ``lat`` and ``lng`` leave its props
and every per-locale override. A location the store already has is
never overwritten: it is the one fact the storefront now reads, and a
section disagreeing with it is exactly the drift being removed.

Settings are written on the historical model, so django-extra-settings'
own save hook does not run; its cache entry is cleared here, or the
store would keep reading the empty value it cached. Runs once per
tenant schema (page_config is TENANT_APPS-only); idempotent, since a
second run finds no coordinates to move.
"""

from __future__ import annotations

from django.db import migrations
from extra_settings.cache import del_cached_setting

COMPONENT_TYPE = "location_map"
COORDINATE_KEYS = ("lat", "lng")
SETTING_BY_KEY = {"lat": "STORE_GEO_LAT", "lng": "STORE_GEO_LNG"}


def _without_coordinates(props: object) -> object:
    if not isinstance(props, dict):
        return props
    return {k: v for k, v in props.items() if k not in COORDINATE_KEYS}


def move_coordinates_to_store_location(apps, schema_editor):
    PageSection = apps.get_model("page_config", "PageSection")
    Setting = apps.get_model("extra_settings", "Setting")

    for section in PageSection.objects.filter(
        component_type=COMPONENT_TYPE
    ).order_by("pk"):
        props = section.props if isinstance(section.props, dict) else {}
        pair = {key: props.get(key) for key in COORDINATE_KEYS}
        if all(value is not None for value in pair.values()):
            stored = {
                key: Setting.objects.filter(name=SETTING_BY_KEY[key]).first()
                for key in COORDINATE_KEYS
            }
            if not any(s is not None and s.value_string for s in stored.values()):
                for key, value in pair.items():
                    name = SETTING_BY_KEY[key]
                    Setting.objects.update_or_create(
                        name=name,
                        defaults={
                            "value_type": "string",
                            "value_string": str(value),
                        },
                    )
                    del_cached_setting(name)

        i18n = section.i18n if isinstance(section.i18n, dict) else {}
        stripped_i18n = {
            locale: (
                {**override, "props": _without_coordinates(override["props"])}
                if isinstance(override, dict) and "props" in override
                else override
            )
            for locale, override in i18n.items()
        }
        stripped_props = _without_coordinates(section.props)
        if stripped_props != section.props or stripped_i18n != i18n:
            section.props = stripped_props
            if i18n:
                section.i18n = stripped_i18n
            section.save(update_fields=["props", "i18n"])


class Migration(migrations.Migration):
    dependencies = [
        ("page_config", "0034_field_verbose_names"),
        ("extra_settings", "0008_auto_20221024_0717"),
    ]

    operations = [
        migrations.RunPython(
            move_coordinates_to_store_location,
            migrations.RunPython.noop,
        ),
    ]
