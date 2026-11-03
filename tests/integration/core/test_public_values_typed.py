"""Every public setting without structure is also served typed.

``GET /settings/public`` carries ``values``: one key per public scalar
setting, in the type its declaration in ``EXTRA_SETTINGS_DEFAULTS`` names,
so a client reads ``giftCardMinAmount`` as a number instead of parsing the
string in the ``settings`` map.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from extra_settings.models import Setting
from rest_framework import status
from rest_framework.test import APIClient

from core.store_setting_shapes import (
    SCALAR_PUBLIC_SETTINGS,
    STRUCTURED_SETTINGS,
)
from core.store_settings import (
    DECLARED_TYPES,
    PUBLIC_SETTING_KEYS,
    declared_default,
)
from tests.utils.openapi import assert_matches_response

pytestmark = pytest.mark.django_db


def _get() -> dict:
    response = APIClient().get(reverse("api-settings-public"))
    assert response.status_code == status.HTTP_200_OK
    return response.json()


def test_every_public_setting_is_typed_exactly_once():
    served_elsewhere = {"CONTACT_ATTACHMENTS_TYPES"}

    assert set(SCALAR_PUBLIC_SETTINGS) == (
        PUBLIC_SETTING_KEYS - STRUCTURED_SETTINGS.keys() - served_elsewhere
    )


def test_the_declared_defaults_are_served_in_their_declared_type():
    values = _get()["values"]

    assert values["giftCardMinAmount"] == declared_default(
        "GIFT_CARD_MIN_AMOUNT"
    )
    assert isinstance(values["giftCardMinAmount"], float)
    assert isinstance(values["returnWindowDays"], int)
    assert isinstance(values["loyaltyNewCustomerBonusEnabled"], bool)
    assert values["dispatchCutoff"] == declared_default("DISPATCH_CUTOFF")


def test_every_type_matches_the_declaration():
    values = _get()["values"]
    kinds = {"int": int, "bool": bool, "decimal": float, "string": str}

    for name in SCALAR_PUBLIC_SETTINGS:
        key = _camel(name)
        if name.startswith("STORE_GEO_"):
            assert values[key] is None
            continue
        assert isinstance(values[key], kinds[DECLARED_TYPES[name]]), name


def _camel(name: str) -> str:
    head, *rest = name.lower().split("_")
    return head + "".join(part.capitalize() for part in rest)


def test_coordinates_are_numbers_and_empty_is_null():
    Setting.objects.update_or_create(
        name="STORE_GEO_LAT",
        defaults={"value_type": Setting.TYPE_STRING, "value_string": "40.5651"},
    )

    values = _get()["values"]

    assert values["storeGeoLat"] == pytest.approx(40.5651)
    assert values["storeGeoLng"] is None


def test_a_stored_value_is_served_in_its_type():
    Setting.objects.update_or_create(
        name="RETURN_WINDOW_DAYS",
        defaults={"value_type": Setting.TYPE_INT, "value_int": 14},
    )

    assert _get()["values"]["returnWindowDays"] == 14


def test_the_typed_value_is_the_string_one_parsed():
    body = _get()
    strings = body["settings"]

    assert (
        str(body["values"]["returnWindowDays"]) == strings["RETURN_WINDOW_DAYS"]
    )
    assert body["values"]["returnFees"] == strings["RETURN_FEES"]


def test_the_response_satisfies_the_published_schema(openapi_schema):
    assert_matches_response(openapi_schema, "/api/v1/settings/public", _get())
