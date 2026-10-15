"""FUNDING_BANNER extra_setting validator (tenant/validators.py).

The EU/ESPA co-funding strip at the top of every page. extra_settings
validators return a boolean — Setting.validate() wraps a falsy result
in its ValidationError.
"""

from __future__ import annotations

import pytest

from tenant.validators import validate_funding_banner_setting

_VALID = {
    "enabled": True,
    "image": "/media/espa.avif",
    "width": 1800,
    "height": 197,
    "alt": "ΕΣΠΑ 2021-2027",
    "link": "https://example.gr/espa",
    "label": "Συγχρηματοδοτείται από την ΕΕ",
}


def test_a_full_banner_is_valid():
    assert validate_funding_banner_setting(_VALID)


def test_a_disabled_banner_needs_only_the_flag():
    assert validate_funding_banner_setting({"enabled": False})


@pytest.mark.parametrize("key", ["width", "height"])
def test_an_image_needs_its_intrinsic_size(key):
    """Without it the storefront cannot reserve the artwork's box, and
    the strip pushes every page down as it loads."""
    assert not validate_funding_banner_setting(
        {k: v for k, v in _VALID.items() if k != key}
    )


def test_a_size_without_an_image_is_rejected():
    assert not validate_funding_banner_setting(
        {"enabled": False, "width": 1800, "height": 197}
    )


@pytest.mark.parametrize(
    "size",
    [0, -1, 10001, 1800.5, "1800", True, None],
)
def test_bad_sizes_rejected(size):
    assert not validate_funding_banner_setting({**_VALID, "width": size})


def test_label_is_optional():
    assert validate_funding_banner_setting(
        {k: v for k, v in _VALID.items() if k != "label"}
    )


@pytest.mark.parametrize("value", [None, "", {}, [], "x", 1])
def test_non_dict_or_missing_enabled_rejected(value):
    assert not validate_funding_banner_setting(value)


def test_enabled_must_be_a_bool():
    assert not validate_funding_banner_setting({**_VALID, "enabled": 1})
    assert not validate_funding_banner_setting({**_VALID, "enabled": "true"})


def test_unknown_keys_rejected():
    assert not validate_funding_banner_setting({**_VALID, "extra": 1})


@pytest.mark.parametrize("key", ["image", "alt", "link"])
def test_required_when_enabled(key):
    value = {k: v for k, v in _VALID.items() if k != key}
    assert not validate_funding_banner_setting(value)


@pytest.mark.parametrize("alt", ["", "   "])
def test_blank_alt_rejected_when_enabled(alt):
    assert not validate_funding_banner_setting({**_VALID, "alt": alt})


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://example.gr/espa",
        "//evil.example/x",
        "espa.avif",
        "",
        "/",
        "https://",
        "/has space",
        5,
    ],
)
@pytest.mark.parametrize("key", ["image", "link"])
def test_bad_urls_rejected(key, url):
    assert not validate_funding_banner_setting({**_VALID, key: url})


@pytest.mark.parametrize("url", ["/espa", "https://example.gr/a?b=1"])
@pytest.mark.parametrize("key", ["image", "link"])
def test_good_urls_accepted(key, url):
    assert validate_funding_banner_setting({**_VALID, key: url})


@pytest.mark.parametrize(
    ("key", "limit"),
    [("image", 1000), ("link", 1000), ("alt", 300), ("label", 120)],
)
def test_length_caps(key, limit):
    prefix = "/" if key in {"image", "link"} else ""
    at_cap = prefix + "a" * (limit - len(prefix))
    assert validate_funding_banner_setting({**_VALID, key: at_cap})
    assert not validate_funding_banner_setting({**_VALID, key: at_cap + "a"})


def test_wrong_types_rejected():
    assert not validate_funding_banner_setting({**_VALID, "alt": 5})
    assert not validate_funding_banner_setting({**_VALID, "label": 5})


_EN = {
    "image": "/media/espa-en.avif",
    "width": 1920,
    "height": 234,
    "alt": "Co-funded by the European Union",
    "label": "See the operation",
}


@pytest.mark.django_db
def test_a_locale_may_replace_the_artwork_and_the_words():
    """The programme publishes the strip per language."""
    assert validate_funding_banner_setting({**_VALID, "i18n": {"en": _EN}})


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["alt", "label"])
def test_a_locale_may_override_one_text_alone(key):
    assert validate_funding_banner_setting(
        {**_VALID, "i18n": {"en": {key: _EN[key]}}}
    )


@pytest.mark.django_db
@pytest.mark.parametrize("missing", ["image", "width", "height"])
def test_a_localized_artwork_carries_its_own_size(missing):
    """The default's size would draw another picture's box."""
    assert not validate_funding_banner_setting(
        {
            **_VALID,
            "i18n": {"en": {k: v for k, v in _EN.items() if k != missing}},
        }
    )


@pytest.mark.django_db
def test_a_localized_artwork_carries_its_own_alt():
    """The default alt describes another picture, in another language."""
    without_alt = {k: v for k, v in _EN.items() if k != "alt"}
    assert not validate_funding_banner_setting(
        {**_VALID, "i18n": {"en": without_alt}}
    )


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["link", "enabled", "i18n", "extra"])
def test_a_locale_overrides_only_the_artwork_and_the_words(key):
    """The link is one path the storefront prefixes per locale."""
    assert not validate_funding_banner_setting(
        {**_VALID, "i18n": {"en": {**_EN, key: "/elsewhere"}}}
    )


@pytest.mark.django_db
def test_the_default_locale_is_not_an_override_key():
    from django.conf import settings

    assert not validate_funding_banner_setting(
        {**_VALID, "i18n": {settings.PARLER_DEFAULT_LANGUAGE_CODE: _EN}}
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "i18n",
    [
        {"fr": _EN},
        {"en": {}},
        {"en": "Co-funded"},
        ["en"],
        {"en": {**_EN, "alt": "   "}},
        {"en": {**_EN, "image": "http://example.gr/espa-en.avif"}},
        {"en": {**_EN, "width": 0}},
        {"en": {**_EN, "label": "a" * 121}},
    ],
)
def test_bad_overrides_rejected(i18n):
    assert not validate_funding_banner_setting({**_VALID, "i18n": i18n})
