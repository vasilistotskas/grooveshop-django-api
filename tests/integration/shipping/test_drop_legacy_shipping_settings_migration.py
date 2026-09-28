"""``0014_drop_legacy_shipping_settings`` — the release-N+1 cleanup that
removes the price settings and country lists ``ShippingRate`` replaced.

Verifies the six settings and ``metadata["supported_countries"]`` go,
that unrelated settings and metadata keys stay, and that a re-run on
already-clean data is a no-op.
"""

from __future__ import annotations

import importlib

import pytest

from shipping.models import ShippingProvider

MIGRATION = "shipping.migrations.0014_drop_legacy_shipping_settings"

pytestmark = pytest.mark.django_db


class _SchemaEditor:
    class connection:
        alias = "default"


def _drop():
    from django.apps import apps

    importlib.import_module(MIGRATION).drop_legacy_shipping_settings(
        apps, _SchemaEditor
    )


def _set_decimal_setting(name: str, value: str) -> None:
    from extra_settings.models import Setting

    Setting.objects.update_or_create(
        name=name,
        defaults={"value_type": Setting.TYPE_DECIMAL, "value_decimal": value},
    )


def test_the_legacy_price_settings_are_deleted_and_the_rest_kept():
    from extra_settings.models import Setting

    legacy = importlib.import_module(MIGRATION).LEGACY_SETTINGS
    for name in legacy:
        _set_decimal_setting(name, "4.20")
    _set_decimal_setting("GIFT_CARD_MIN_AMOUNT", "10.00")

    _drop()

    assert not Setting.objects.filter(name__in=legacy).exists()
    assert Setting.objects.filter(name="GIFT_CARD_MIN_AMOUNT").exists()


def test_supported_countries_is_popped_and_other_metadata_kept():
    provider = ShippingProvider.objects.get(code="boxnow")
    provider.metadata = {
        "supported_countries": ["GR"],
        "locker_picker_kind": "boxnow_widget",
    }
    provider.save(update_fields=["metadata"])

    _drop()

    provider.refresh_from_db()
    assert provider.metadata == {"locker_picker_kind": "boxnow_widget"}


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
