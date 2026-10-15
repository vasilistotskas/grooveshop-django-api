"""Setting rows follow settings.py's description and validator
(tenant/settings_metadata.py).

django-extra-settings copies both into a row only when it creates it, and
validates against the stored path, so a store provisioned before a
validator existed never ran it.
"""

from __future__ import annotations

import pytest
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models.signals import post_migrate
from extra_settings.models import Setting

from tenant.settings_metadata import sync_setting_metadata

pytestmark = pytest.mark.django_db

_LATITUDE = next(
    e for e in settings.EXTRA_SETTINGS_DEFAULTS if e["name"] == "STORE_GEO_LAT"
)


def _stale_latitude(value: str = "40.565129") -> Setting:
    row = Setting.objects.get(name="STORE_GEO_LAT")
    Setting.objects.filter(pk=row.pk).update(
        description="Physical store latitude (decimal degrees, as a string).",
        validator=None,
        value_string=value,
    )
    return row


def test_an_existing_row_gets_the_current_description_and_validator():
    row = _stale_latitude()

    assert sync_setting_metadata() >= 1

    row.refresh_from_db()
    assert row.description == _LATITUDE["description"]
    assert row.validator == _LATITUDE["validator"]


def test_the_value_is_never_touched_even_when_the_new_validator_refuses_it():
    """The admin asks for a valid one at the next edit; data is not lost."""
    row = _stale_latitude(value="37.9oops")

    sync_setting_metadata()

    row.refresh_from_db()
    assert row.value == "37.9oops"
    with pytest.raises(ValidationError):
        row.full_clean()


def test_a_second_run_changes_nothing():
    _stale_latitude()
    sync_setting_metadata()

    assert sync_setting_metadata() == 0


def test_it_runs_after_every_migrate_of_the_settings_app():
    """Each deploy's migrate_schemas is what brings every store in line."""
    sender = apps.get_app_config("extra_settings")
    keys = [key for key, *_ in post_migrate.receivers]

    assert ("tenant.sync_setting_metadata", id(sender)) in keys
