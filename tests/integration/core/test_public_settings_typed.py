"""The structured store settings are served typed, from their one definition.

``GET /settings/public`` carries ``announcementBar``, ``authPanel``,
``fundingBanner``, ``storeOffices``, ``businessHours`` and
``contactAttachmentsTypes`` next to the string map. The classes that
validate a save (``core/store_setting_shapes.py``) are the ones that
describe and serve the value, so what is served is what was accepted and
what ``schema.yml`` says.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from extra_settings.models import Setting
from rest_framework import serializers, status
from rest_framework.test import APIClient

from core.store_setting_shapes import (
    STRUCTURED_SETTINGS,
    is_valid_structured,
    validated_structured,
)
from tests.utils.openapi import assert_matches_response

pytestmark = pytest.mark.django_db

PATH = "/api/v1/settings/public"

# Shaped like the values the production stores hold.
ANNOUNCEMENT = {
    "enabled": True,
    "text": "Free shipping over 39",
    "shortText": "Free shipping 39+",
    "code": "FREESHIP",
    "i18n": {"en": {"text": "Free shipping", "shortText": "Free 39+"}},
}
AUTH_PANEL = {
    "imageUrl": "media/uploads/hero.jpg",
    "tagline": "Welcome",
    "i18n": {"en": {"tagline": "Welcome back"}},
}
FUNDING = {
    "enabled": True,
    "image": "/espa.png",
    "width": 800,
    "height": 120,
    "alt": "ESPA",
    "link": "/espa",
    "i18n": {
        "en": {"image": "/espa-en.png", "width": 800, "height": 120, "alt": "E"}
    },
}
OFFICES = [
    {
        "label": "Head office",
        "street": "1 Main St",
        "postal": "11111",
        "city": "Athens",
        "phones": ["2101234567"],
        "i18n": {"en": {"city": "Athens"}},
    }
]
HOURS = {
    "timezone": "Europe/Athens",
    "schedule": {
        **{
            day: {"opens": "09:00", "closes": "17:00"} for day in ("mon", "tue")
        },
        **{day: None for day in ("wed", "thu", "fri", "sat", "sun")},
    },
}
EXAMPLES = {
    "ANNOUNCEMENT_BAR": ANNOUNCEMENT,
    "AUTH_PANEL": AUTH_PANEL,
    "FUNDING_BANNER": FUNDING,
    "STORE_OFFICES": OFFICES,
    "BUSINESS_HOURS": HOURS,
}


def _store(name: str, value: object) -> None:
    Setting.objects.update_or_create(
        name=name,
        defaults={"value_type": Setting.TYPE_JSON, "value_json": value},
    )


def _get() -> dict:
    response = APIClient().get(reverse("api-settings-public"))
    assert response.status_code == status.HTTP_200_OK
    return response.json()


def test_every_structured_setting_has_an_example():
    assert set(EXAMPLES) == set(STRUCTURED_SETTINGS)


@pytest.mark.parametrize(("name", "value"), EXAMPLES.items())
def test_a_real_shaped_value_is_valid(name, value):
    assert is_valid_structured(name, value)


def test_what_is_served_is_what_was_stored():
    for name, value in EXAMPLES.items():
        _store(name, value)

    body = _get()

    assert body["announcementBar"] == ANNOUNCEMENT
    assert body["authPanel"] == AUTH_PANEL
    assert body["fundingBanner"] == FUNDING
    assert body["storeOffices"] == OFFICES
    assert body["businessHours"] == HOURS


def test_nothing_configured_is_null_or_empty():
    for name in ("ANNOUNCEMENT_BAR", "AUTH_PANEL", "BUSINESS_HOURS"):
        _store(name, {})
    _store("STORE_OFFICES", [])

    body = _get()

    assert body["announcementBar"] is None
    assert body["authPanel"] is None
    assert body["businessHours"] is None
    assert body["storeOffices"] == []


def test_the_banner_is_never_null():
    Setting.objects.filter(name="FUNDING_BANNER").delete()

    assert _get()["fundingBanner"] == {"enabled": False}


def test_a_stored_value_that_is_not_its_shape_fails_loudly():
    """No fallback: the admin refuses such a value and the deploy check
    refuses a store that holds one, so reaching here is a bug."""
    _store("ANNOUNCEMENT_BAR", {"enabled": "yes"})

    response = APIClient().get(reverse("api-settings-public"))

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json()["code"] == "settings_unavailable"


def test_contact_attachment_types_are_a_list():
    _store_text("CONTACT_ATTACHMENTS_TYPES", "application/pdf, Image/PNG")

    assert _get()["contactAttachmentsTypes"] == [
        "application/pdf",
        "image/png",
    ]


def _store_text(name: str, value: str) -> None:
    Setting.objects.update_or_create(
        name=name,
        defaults={"value_type": Setting.TYPE_STRING, "value_string": value},
    )


def test_the_response_satisfies_the_published_schema(openapi_schema):
    for name, value in EXAMPLES.items():
        _store(name, value)

    assert_matches_response(openapi_schema, PATH, _get())


@pytest.mark.parametrize(
    ("name", "value"),
    [
        # a key that is already snake_case would be missed by the client
        ("ANNOUNCEMENT_BAR", {"enabled": True, "text": "x", "short_text": "y"}),
        # a wrong type is refused, not coerced
        ("ANNOUNCEMENT_BAR", {"enabled": "true", "text": "x"}),
        ("ANNOUNCEMENT_BAR", {"enabled": True, "text": 5}),
        ("FUNDING_BANNER", {"enabled": False, "width": True, "image": "/a"}),
        ("FUNDING_BANNER", {"enabled": False, "image": "/a", "width": "800"}),
        ("BUSINESS_HOURS", {**HOURS, "timezone": 3}),
        ("STORE_OFFICES", [{"label": "a", "street": "b", "phones": [5]}]),
        # an unknown key anywhere, however deep
        ("STORE_OFFICES", [{"label": "a", "street": "b", "extra": 1}]),
        ("AUTH_PANEL", {"i18n": {"en": {"tagline": "x", "other": "y"}}}),
        (
            "BUSINESS_HOURS",
            {**HOURS, "schedule": {**HOURS["schedule"], "x": 1}},
        ),
    ],
)
def test_the_wrong_shape_is_refused(name, value):
    assert not is_valid_structured(name, value)
    with pytest.raises(serializers.ValidationError):
        validated_structured(name, value)
