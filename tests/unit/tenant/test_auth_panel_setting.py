"""AUTH_PANEL extra_setting validator (tenant/validators.py).

The photo and line beside the sign-in pages. extra_settings validators
return a boolean — Setting.validate() wraps a falsy result in its
ValidationError. The tagline is per-locale copy (the same partial
``i18n`` override ``ANNOUNCEMENT_BAR`` uses); the photo is one stored
media path for every language.
"""

from __future__ import annotations

import pytest
from django.conf import settings

from tenant.validators import validate_auth_panel_setting

_VALID = {
    "imageUrl": "media/shop/uploads/pages/auth.avif",
    "tagline": "Ο ήχος που σου ταιριάζει",
    "i18n": {"en": {"tagline": "Sound that fits"}},
}


def test_empty_value_is_valid_nothing_configured():
    assert validate_auth_panel_setting(None)
    assert validate_auth_panel_setting("")
    assert validate_auth_panel_setting({})


@pytest.mark.django_db
def test_a_full_panel_is_valid():
    assert validate_auth_panel_setting(_VALID)


def test_either_half_alone_is_valid():
    assert validate_auth_panel_setting({"imageUrl": "media/a.avif"})
    assert validate_auth_panel_setting({"tagline": "Hello"})


def test_non_dict_and_unknown_keys_rejected():
    assert not validate_auth_panel_setting("a photo")
    assert not validate_auth_panel_setting([_VALID])
    assert not validate_auth_panel_setting({**_VALID, "image_url": "x"})
    assert not validate_auth_panel_setting({**_VALID, "color": "ink"})


def test_image_is_a_bounded_string():
    assert not validate_auth_panel_setting({"imageUrl": 5})
    assert not validate_auth_panel_setting({"imageUrl": "x" * 1001})
    assert validate_auth_panel_setting({"imageUrl": "x" * 1000})


def test_tagline_is_a_bounded_string():
    assert not validate_auth_panel_setting({"tagline": 5})
    assert not validate_auth_panel_setting({"tagline": "x" * 201})
    assert validate_auth_panel_setting({"tagline": "x" * 200})


@pytest.mark.django_db
def test_i18n_overrides_only_the_tagline_of_a_served_locale():
    for i18n in (
        {"en": {"imageUrl": "media/b.avif"}},
        {"en": {"tagline": "Hi", "extra": "x"}},
        {"en": {}},
        {"en": {"tagline": 5}},
        {"en": {"tagline": "x" * 201}},
        {"fr": {"tagline": "Salut"}},
        ["en"],
    ):
        assert not validate_auth_panel_setting({**_VALID, "i18n": i18n}), i18n


@pytest.mark.django_db
def test_the_default_locale_is_not_an_override_key():
    """Its wording IS ``tagline`` — a second copy could only drift."""
    assert not validate_auth_panel_setting(
        {
            **_VALID,
            "i18n": {
                settings.PARLER_DEFAULT_LANGUAGE_CODE: {"tagline": "Δεύτερο"}
            },
        }
    )
