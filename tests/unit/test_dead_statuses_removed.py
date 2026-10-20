"""Statuses nothing sets are not offered.

``VivaWebhookEvent`` "failed": a handler that raises rolls its row back so
Viva's retry gets a fresh attempt (a stored row would dedup it for good),
so no row can ever hold it. Meta CAPI "skipped": a dispatch that does not
apply (CAPI off, ad consent declined) writes no row. And the
``FAVOURITES_ENABLED`` description no longer promises emails.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as real_apps
from django.conf import settings
from extra_settings.models import Setting

from meta_capi.models import MetaCapiEventStatus
from order.models.viva_webhook_event import VivaWebhookEvent

MIGRATION = "product.migrations.0055_favourites_setting_description"


class _SchemaEditor:
    class connection:
        alias = "default"


def test_a_viva_webhook_event_cannot_be_recorded_as_failed():
    assert {value for value, _label in VivaWebhookEvent.OUTCOME_CHOICES} == {
        "processed",
        "skipped",
    }
    assert not hasattr(VivaWebhookEvent, "OUTCOME_FAILED")


def test_meta_capi_has_no_skipped_status():
    assert set(MetaCapiEventStatus.values) == {"pending", "sent", "failed"}


def _seeded_description() -> str:
    [entry] = [
        e
        for e in settings.EXTRA_SETTINGS_DEFAULTS
        if e["name"] == "FAVOURITES_ENABLED"
    ]
    return entry["description"]


def test_the_favourites_setting_promises_notifications_not_emails():
    description = _seeded_description()

    assert "notifications" in description
    assert "emails" not in description.replace("no emails", "")


@pytest.mark.django_db
class TestFavouritesDescriptionMigration:
    def test_rewrites_the_old_text_on_existing_stores(self):
        migration = importlib.import_module(MIGRATION)
        row, _ = Setting.objects.update_or_create(
            name="FAVOURITES_ENABLED",
            defaults={
                "value_type": Setting.TYPE_BOOL,
                "value_bool": True,
                "description": migration.OLD,
            },
        )

        migration.forwards(real_apps, _SchemaEditor)

        row.refresh_from_db()
        assert row.description == migration.NEW == _seeded_description()

    def test_keeps_a_description_the_operator_wrote(self):
        migration = importlib.import_module(MIGRATION)
        row, _ = Setting.objects.update_or_create(
            name="FAVOURITES_ENABLED",
            defaults={
                "value_type": Setting.TYPE_BOOL,
                "value_bool": True,
                "description": "Our wishlist",
            },
        )

        migration.forwards(real_apps, _SchemaEditor)

        row.refresh_from_db()
        assert row.description == "Our wishlist"
