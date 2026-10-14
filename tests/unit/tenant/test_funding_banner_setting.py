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
    "alt": "ΕΣΠΑ 2021-2027",
    "link": "https://example.gr/espa",
    "label": "Συγχρηματοδοτείται από την ΕΕ",
}


def test_a_full_banner_is_valid():
    assert validate_funding_banner_setting(_VALID)


def test_a_disabled_banner_needs_only_the_flag():
    assert validate_funding_banner_setting({"enabled": False})


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
