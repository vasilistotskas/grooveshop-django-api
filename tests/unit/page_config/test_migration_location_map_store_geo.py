"""Exercises page_config/migrations/0035_location_map_reads_store_geo.

The location map now pins the store's own location (STORE_GEO_LAT /
STORE_GEO_LNG), so the coordinates sections carried leave them. Three
properties matter, and each has a test that fails when it breaks:

- a store with no location yet adopts its map's pin, so no map loses it;
- a location the store already has is never overwritten by a section;
- the coordinates leave the props and every per-locale override, once.
"""

import importlib

from django.apps import apps as live_apps
from django.test import TestCase
from extra_settings.models import Setting

from page_config.models import ComponentType, PageLayout, PageSection

migration_module = importlib.import_module(
    "page_config.migrations.0035_location_map_reads_store_geo"
)


def _run():
    migration_module.move_coordinates_to_store_location(live_apps, None)


def _store_location():
    return {
        name: Setting.objects.get(name=name).value
        for name in ("STORE_GEO_LAT", "STORE_GEO_LNG")
    }


def _set_store_location(lat: str, lng: str):
    for name, value in (("STORE_GEO_LAT", lat), ("STORE_GEO_LNG", lng)):
        Setting.objects.update_or_create(
            name=name,
            defaults={"value_type": "string", "value_string": value},
        )


class TestLocationMapReadsStoreGeo(TestCase):
    def setUp(self):
        layout = PageLayout.objects.create(
            page_type="contact", title="Contact", is_published=True
        )
        self.section = PageSection.objects.create(
            layout=layout,
            component_type=ComponentType.LOCATION_MAP,
            title="",
            sort_order=0,
            props={
                "lat": 40.565129,
                "lng": 22.119096,
                "address": "Φυτειά Βέροιας",
                "surface": "muted",
            },
            i18n={
                "en": {
                    "props": {
                        "lat": 40.565129,
                        "lng": 22.119096,
                        "address": "Fyteia, Veria",
                    }
                }
            },
        )

    def test_a_store_without_a_location_adopts_its_map_s_pin(self):
        _set_store_location("", "")

        _run()

        assert _store_location() == {
            "STORE_GEO_LAT": "40.565129",
            "STORE_GEO_LNG": "22.119096",
        }

    def test_a_store_s_own_location_is_never_overwritten(self):
        _set_store_location("40.6403", "22.9439")

        _run()

        assert _store_location() == {
            "STORE_GEO_LAT": "40.6403",
            "STORE_GEO_LNG": "22.9439",
        }

    def test_the_coordinates_leave_the_props_and_every_override(self):
        _set_store_location("40.6403", "22.9439")

        _run()

        self.section.refresh_from_db()
        assert self.section.props == {
            "address": "Φυτειά Βέροιας",
            "surface": "muted",
        }
        assert self.section.i18n == {
            "en": {"props": {"address": "Fyteia, Veria"}}
        }

    def test_a_second_run_changes_nothing(self):
        _set_store_location("", "")
        _run()
        self.section.refresh_from_db()
        after_first = (self.section.props, self.section.i18n, _store_location())

        _set_store_location("1", "2")
        _run()

        self.section.refresh_from_db()
        assert (self.section.props, self.section.i18n) == after_first[:2]
        assert _store_location() == {"STORE_GEO_LAT": "1", "STORE_GEO_LNG": "2"}
