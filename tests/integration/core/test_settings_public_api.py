"""Integration tests for GET /api/v1/settings/public.

The storefront's ONE settings read per render. It must expose exactly
the anonymously readable keys — the same allow-list the single-key
endpoint enforces — encoded the same way, so a consumer parses one
shape whichever endpoint it reads.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse
from extra_settings.models import Setting
from rest_framework import status
from rest_framework.test import APIClient

from core.api.views import PUBLIC_SETTING_KEYS

pytestmark = pytest.mark.django_db


def _public_settings() -> dict[str, str]:
    response = APIClient().get(reverse("api-settings-public"))
    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert set(payload) == {"settings"}
    return payload["settings"]


def test_anonymous_caller_gets_every_public_key_with_a_row():
    values = _public_settings()
    seeded_public = set(
        Setting.objects.filter(name__in=PUBLIC_SETTING_KEYS).values_list(
            "name", flat=True
        )
    )
    # Every public row is served, nothing else is.
    assert set(values) == seeded_public
    assert seeded_public, "the fixture DB seeds public settings"
    assert all(isinstance(v, str) for v in values.values())


def test_a_private_setting_row_is_never_exposed():
    Setting.objects.create(
        name="PRIVATE_PROBE_SETTING",
        value_type=Setting.TYPE_STRING,
        value_string="hidden",
    )

    assert "PRIVATE_PROBE_SETTING" not in _public_settings()


def test_values_match_the_single_key_endpoint():
    """Parity with ``settings/get``: same string for every key."""
    values = _public_settings()
    client = APIClient()
    for key, value in values.items():
        single = client.get(reverse("api-settings-get"), {"key": key})
        assert single.status_code == status.HTTP_200_OK, key
        assert single.json()["value"] == value, key


def test_json_typed_settings_are_json_encoded():
    hours = {"mon": [["09:00", "17:00"]], "tz": "Europe/Athens"}
    Setting.objects.update_or_create(
        name="BUSINESS_HOURS",
        defaults={"value_type": Setting.TYPE_JSON, "value_json": hours},
    )

    assert json.loads(_public_settings()["BUSINESS_HOURS"]) == hours


def test_a_row_without_a_value_is_omitted():
    """A null value is "no value": the caller applies its own default
    rather than parsing the string ``"None"``.

    Only the date/time/duration/url columns are nullable in
    django-extra-settings, so the probe is a datetime-typed row.
    """
    Setting.objects.update_or_create(
        name="STORE_GEO_LAT",
        defaults={
            "value_type": Setting.TYPE_DATETIME,
            "value_datetime": None,
        },
    )

    assert "STORE_GEO_LAT" not in _public_settings()
