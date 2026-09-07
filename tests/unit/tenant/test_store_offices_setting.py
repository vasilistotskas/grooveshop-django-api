"""STORE_OFFICES extra_setting validator (tenant/validators.py).

extra_settings validators return a boolean — Setting.validate() wraps a
falsy result in its ValidationError.

This setting exists because the platform previously modelled only ONE
address per store, the ``INVOICE_SELLER_*`` registered seat, and that is
not what a storefront publishes: a merchant's public offices are often
neither the same place as the seat nor a single place. Δelta Σigma has
two (Thessaloniki and Attica) and a registered seat at a third address.
"""

from __future__ import annotations

from tenant.validators import validate_store_offices_setting

_VALID = [
    {
        "label": "Θεσσαλονίκη",
        "street": "Γ. Ρίτσου 7",
        "area": "Καλαμαριά",
        "postal": "551 32",
        "city": "Θεσσαλονίκη",
        "phones": ["2310 924 440", "2310 934 169"],
        "i18n": {
            "en": {
                "label": "Thessaloniki",
                "street": "7 G. Ritsou St.",
                "area": "Kalamaria",
                "city": "Thessaloniki",
            }
        },
    }
]


def test_empty_value_is_valid_feature_unset():
    assert validate_store_offices_setting(None)
    assert validate_store_offices_setting("")
    assert validate_store_offices_setting([])


def test_a_full_office_passes():
    assert validate_store_offices_setting(_VALID)


def test_label_and_street_are_required():
    for missing in ("label", "street"):
        office = {k: v for k, v in _VALID[0].items() if k != missing}
        assert not validate_store_offices_setting([office]), missing


def test_a_blank_required_field_is_refused():
    office = {**_VALID[0], "street": "   "}

    assert not validate_store_offices_setting([office])


def test_the_optional_fields_may_be_absent():
    office = {"label": "Αττική", "street": "Ιλισίων 23"}

    assert validate_store_offices_setting([office])


def test_unknown_keys_are_refused():
    """A typo must fail here rather than render as nothing."""
    office = {**_VALID[0], "postcode": "551 32"}

    assert not validate_store_offices_setting([office])


def test_phones_must_be_non_empty_strings():
    assert not validate_store_offices_setting(
        [{**_VALID[0], "phones": ["2310 924 440", ""]}]
    )
    assert not validate_store_offices_setting(
        [{**_VALID[0], "phones": [2310924440]}]
    )
    assert not validate_store_offices_setting(
        [{**_VALID[0], "phones": "2310 924 440"}]
    )


def test_the_i18n_overlay_covers_text_only():
    """Numbers are locale-independent.

    A postcode and a phone number read the same in every language, so
    letting a locale override them would only invite the two copies to
    drift — the same rule as ``PageSection.i18n``.
    """
    assert not validate_store_offices_setting(
        [{**_VALID[0], "i18n": {"en": {"postal": "55132"}}}]
    )
    assert not validate_store_offices_setting(
        [{**_VALID[0], "i18n": {"en": {"phones": ["+30 2310 924 440"]}}}]
    )


def test_a_locale_the_store_does_not_serve_is_refused():
    assert not validate_store_offices_setting(
        [{**_VALID[0], "i18n": {"fr": {"city": "Thessalonique"}}}]
    )


def test_the_default_locale_is_not_a_valid_overlay_key():
    """Its values ARE the entry's own fields."""
    assert not validate_store_offices_setting(
        [{**_VALID[0], "i18n": {"el": {"city": "Θεσσαλονίκη"}}}]
    )


def test_a_partial_overlay_is_allowed():
    office = {**_VALID[0], "i18n": {"en": {"city": "Thessaloniki"}}}

    assert validate_store_offices_setting(office and [office])


def test_the_shape_itself_must_be_a_bounded_list():
    assert not validate_store_offices_setting({"label": "x", "street": "y"})
    assert not validate_store_offices_setting(["Θεσσαλονίκη"])
    assert not validate_store_offices_setting([_VALID[0]] * 11)
