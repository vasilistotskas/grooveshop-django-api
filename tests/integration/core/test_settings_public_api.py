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
from core.store_settings import declared_default
from tenant.features import feature_setting_keys, preference_setting_keys

pytestmark = pytest.mark.django_db


def _public_settings() -> dict[str, str]:
    response = APIClient().get(reverse("api-settings-public"))
    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert set(payload) == {"settings"}
    return payload["settings"]


def test_anonymous_caller_gets_every_public_key():
    values = _public_settings()

    assert set(values) == PUBLIC_SETTING_KEYS
    assert all(isinstance(v, str) for v in values.values())


def test_a_key_without_a_row_is_served_with_its_declared_value():
    Setting.objects.filter(name="GIFT_CARD_MAX_AMOUNT").delete()

    assert _public_settings()["GIFT_CARD_MAX_AMOUNT"] == str(
        declared_default("GIFT_CARD_MAX_AMOUNT")
    )


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


def test_a_row_without_a_value_reads_as_its_declared_value():
    """A null value is "no value", as in ``Setting.get``: the key is
    served with the value it is declared with, never the string
    ``"None"``.

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

    assert _public_settings()["STORE_GEO_LAT"] == str(
        declared_default("STORE_GEO_LAT")
    )


def test_the_demo_account_credentials_are_public_and_empty_by_default():
    """A demo store shows shared credentials on its login page.

    They are published deliberately — the card exists so a prospect can
    see the logged-in storefront without signing up — which is why both
    values ship empty: a store that has not asked for this must expose
    nothing, and the seeder writes them only on a tenant flagged
    ``is_demo``. Whether the card shows is the ``demo_account``
    preference, served in ``TenantConfig.preferences``, not here.
    """
    values = _public_settings()

    assert "DEMO_ACCOUNT_ENABLED" not in values
    assert values["DEMO_ACCOUNT_EMAIL"] == ""
    assert values["DEMO_ACCOUNT_PASSWORD"] == ""


def test_the_announcement_bar_is_public_and_empty_by_default():
    """Operator text rendered on every page, so public by construction."""
    values = _public_settings()

    assert values["ANNOUNCEMENT_BAR"] == "{}"


def test_the_funding_banner_is_public_and_disabled_by_default():
    """Legally required co-funding strip shown to every visitor."""
    values = _public_settings()

    assert json.loads(values["FUNDING_BANNER"]) == {"enabled": False}


def test_the_auth_panel_is_public_and_empty_by_default():
    """Shown on the sign-in pages before anyone has authenticated."""
    values = _public_settings()

    assert values["AUTH_PANEL"] == "{}"


def test_no_store_switch_is_public():
    """A store feature is served in ``TenantConfig.features`` and a
    preference in ``TenantConfig.preferences``, Django's one answer for
    each; published here too, a client could read the switch instead and
    disagree with it (the frozen webside storefront did until 2026-10-23).
    """
    switches = feature_setting_keys() | preference_setting_keys()

    assert PUBLIC_SETTING_KEYS.isdisjoint(switches), sorted(
        PUBLIC_SETTING_KEYS & switches
    )
    assert switches.isdisjoint(_public_settings())
