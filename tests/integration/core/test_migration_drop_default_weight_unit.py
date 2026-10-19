"""``DEFAULT_WEIGHT_UNIT`` is gone from the seed list and from stores.

No code read it (a product's weight carries its own unit), so a switch
for it in the admin Settings list changed nothing. ``core/0012`` deletes
the row from stores that already have it, and only that row.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as real_apps
from django.conf import settings
from extra_settings.models import Setting

MIGRATION = "core.migrations.0012_drop_default_weight_unit_setting"


class _SchemaEditor:
    class connection:
        alias = "default"


@pytest.mark.django_db
class TestDropDefaultWeightUnit:
    def test_seed_list_does_not_offer_it(self):
        names = {entry["name"] for entry in settings.EXTRA_SETTINGS_DEFAULTS}

        assert "DEFAULT_WEIGHT_UNIT" not in names

    def test_deletes_the_row_and_nothing_else(self):
        Setting.objects.update_or_create(
            name="DEFAULT_WEIGHT_UNIT",
            defaults={"value_type": Setting.TYPE_STRING, "value_string": "kg"},
        )
        keep, _ = Setting.objects.update_or_create(
            name="CART_ABANDONED_HOURS",
            defaults={"value_type": Setting.TYPE_INT, "value_int": 24},
        )

        importlib.import_module(MIGRATION).drop_unused_settings(
            real_apps, _SchemaEditor
        )

        assert not Setting.objects.filter(name="DEFAULT_WEIGHT_UNIT").exists()
        assert Setting.objects.filter(pk=keep.pk).exists()
