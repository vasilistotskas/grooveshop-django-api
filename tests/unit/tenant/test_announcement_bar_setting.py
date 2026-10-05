"""ANNOUNCEMENT_BAR extra_setting validator (tenant/validators.py).

extra_settings validators return a boolean — Setting.validate() wraps a
falsy result in its ValidationError. The bar renders above the header on
every page of a store, so what an operator can put in it is bounded
here: one line of text, an optional link and icon, a semantic colour,
and per-locale wording through the same partial ``i18n`` override the
page builder and STORE_OFFICES use.
"""

from __future__ import annotations

import pytest

from tenant.validators import validate_announcement_bar_setting

_VALID = {
    "enabled": True,
    "text": "Δωρεάν αποστολή από 39€",
    "i18n": {
        "en": {
            "text": "Free shipping over 39€",
            "shortText": "Free shipping 39€+",
        }
    },
    "link": "/offers",
    "icon": "i-heroicons-truck",
    "shortText": "Δωρεάν αποστολή 39€+",
    "code": "FREESHIP",
    "color": "secondary",
    "dismissible": True,
    "id": "free-shipping-2026",
}


def test_empty_value_is_valid_no_bar():
    assert validate_announcement_bar_setting(None)
    assert validate_announcement_bar_setting("")
    assert validate_announcement_bar_setting({})


@pytest.mark.django_db
def test_a_full_bar_is_valid():
    assert validate_announcement_bar_setting(_VALID)


def test_non_dict_rejected():
    assert not validate_announcement_bar_setting("Free shipping!")
    assert not validate_announcement_bar_setting([_VALID])


def test_unknown_keys_rejected():
    assert not validate_announcement_bar_setting({**_VALID, "speed": "fast"})


def test_enabled_must_be_a_boolean():
    assert not validate_announcement_bar_setting({"enabled": "yes"})


def test_an_enabled_bar_needs_something_to_say():
    """Otherwise the store gets a blank strip above its header."""
    assert not validate_announcement_bar_setting({"enabled": True})
    assert not validate_announcement_bar_setting(
        {"enabled": True, "text": "   "}
    )
    # Off with no text is how a merchant parks a bar between campaigns.
    assert validate_announcement_bar_setting({"enabled": False, "text": ""})


def test_text_is_capped_at_one_line():
    assert not validate_announcement_bar_setting(
        {"enabled": True, "text": "x" * 201}
    )


def test_link_must_be_internal_or_https():
    for link in ("javascript:alert(1)", "http://example.com", "example.com"):
        assert not validate_announcement_bar_setting({**_VALID, "link": link})
    for link in ("/offers", "https://example.com/sale"):
        assert validate_announcement_bar_setting({**_VALID, "link": link})


def test_icon_must_look_like_an_icon_name():
    assert not validate_announcement_bar_setting(
        {**_VALID, "icon": "truck.png"}
    )
    assert validate_announcement_bar_setting({**_VALID, "icon": "i-lucide-tag"})


def test_colour_is_one_of_the_semantic_set():
    assert not validate_announcement_bar_setting({**_VALID, "color": "#ff0000"})
    assert not validate_announcement_bar_setting({**_VALID, "color": "teal"})
    assert validate_announcement_bar_setting({**_VALID, "color": "warning"})


def test_id_is_bounded():
    assert not validate_announcement_bar_setting({**_VALID, "id": "x" * 65})


@pytest.mark.django_db
def test_i18n_overrides_only_the_text_of_a_served_locale():
    assert not validate_announcement_bar_setting(
        {**_VALID, "i18n": {"en": {"heading": "Sale"}}}
    )
    assert not validate_announcement_bar_setting(
        {**_VALID, "i18n": {"fr": {"text": "Soldes"}}}
    )
    assert not validate_announcement_bar_setting({**_VALID, "i18n": ["en"]})


@pytest.mark.django_db
def test_the_default_locale_is_not_an_override_key():
    """Its wording IS ``text`` — a second copy could only drift."""
    from django.conf import settings

    assert not validate_announcement_bar_setting(
        {
            **_VALID,
            "i18n": {
                settings.PARLER_DEFAULT_LANGUAGE_CODE: {"text": "Δεύτερο"}
            },
        }
    )


def test_code_is_one_bounded_token():
    """It is shown set apart and copied, so it cannot be a sentence."""
    for code in ("TWO WORDS", "", "x" * 41, 7, ["A"]):
        assert not validate_announcement_bar_setting({**_VALID, "code": code})
    assert validate_announcement_bar_setting({**_VALID, "code": "X" * 40})
    assert validate_announcement_bar_setting({**_VALID, "code": "SAVE-5_NOW"})


def test_short_text_is_bounded_and_a_string():
    assert not validate_announcement_bar_setting(
        {**_VALID, "shortText": "x" * 81}
    )
    assert not validate_announcement_bar_setting({**_VALID, "shortText": 5})
    assert validate_announcement_bar_setting({**_VALID, "shortText": "x" * 80})


def test_the_old_snake_case_spelling_is_not_a_key():
    """The wire key is ``shortText``: the storefront reads the stored
    JSON as written, so a second spelling would be silently ignored."""
    assert not validate_announcement_bar_setting(
        {**_VALID, "short_text": "Free shipping"}
    )


@pytest.mark.django_db
def test_a_locale_may_override_short_text_alone():
    assert validate_announcement_bar_setting(
        {**_VALID, "i18n": {"en": {"shortText": "Free shipping 39€+"}}}
    )


@pytest.mark.django_db
def test_a_locale_override_is_bounded_and_never_carries_the_code():
    """A promo code is the same string in every language."""
    for override in (
        {"code": "FREESHIP"},
        {"shortText": "x" * 81},
        {"text": "x" * 201},
        {"shortText": 3},
        {},
    ):
        assert not validate_announcement_bar_setting(
            {**_VALID, "i18n": {"en": override}}
        ), override
