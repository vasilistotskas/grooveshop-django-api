"""``0015_drop_map_metadata`` — removes the unread Leaflet map keys
``0004`` seeded into ACS provider metadata.

Verifies the three keys go, that every other metadata key stays, and
that a re-run on already-clean data is a no-op.
"""

from __future__ import annotations

import importlib

import pytest

from shipping.models import ShippingProvider

MIGRATION = "shipping.migrations.0015_drop_map_metadata"

pytestmark = pytest.mark.django_db


class _SchemaEditor:
    class connection:
        alias = "default"


def _drop():
    from django.apps import apps

    importlib.import_module(MIGRATION).drop_map_metadata(apps, _SchemaEditor)


def test_the_map_keys_are_dropped_and_the_rest_kept():
    provider = ShippingProvider.objects.get(code="acs")
    provider.metadata = {
        "tile_provider": {"light": {"url": "https://example.test/{z}"}},
        "default_map_center": [37.9838, 23.7275],
        "default_map_zoom": 11,
        "nearest_limit": 20,
        "locker_picker_kind": "acs_db_picker",
    }
    provider.save(update_fields=["metadata"])

    _drop()

    provider.refresh_from_db()
    assert provider.metadata == {
        "nearest_limit": 20,
        "locker_picker_kind": "acs_db_picker",
    }


def test_a_rerun_on_clean_data_changes_nothing():
    _drop()
    before = {
        p.code: p.metadata for p in ShippingProvider.objects.order_by("code")
    }

    _drop()

    after = {
        p.code: p.metadata for p in ShippingProvider.objects.order_by("code")
    }
    assert after == before
