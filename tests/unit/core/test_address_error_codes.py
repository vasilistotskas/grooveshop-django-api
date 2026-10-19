"""Address rule messages keep a stable code through ``ValidationError``.

``core.validators.address`` serves a DRF serializer and a model ``clean()``;
each message is an ``ErrorDetail`` so DRF carries its code to the wire while
Django still sees a string.
"""

from __future__ import annotations

from types import SimpleNamespace

from rest_framework import serializers

from core.validators import address as address_rules


def _country(*, pattern=r"\d{5}", regions=False):
    return SimpleNamespace(
        alpha_2="GR",
        postal_code_pattern=pattern,
        postal_code_example="12345",
        regions=SimpleNamespace(exists=lambda: regions),
    )


def _codes(errors):
    try:
        raise serializers.ValidationError(errors)
    except serializers.ValidationError as exc:
        return {
            field: [item["code"] for item in details]
            for field, details in exc.get_full_details().items()
        }


def test_every_address_rule_names_its_code():
    errors = address_rules.address_errors(
        country=_country(regions=True),
        street="12",
        street_number="70300",
        zipcode="abc",
        region=None,
    )

    assert _codes(errors) == {
        "zipcode": ["invalid_postcode"],
        "street": ["street_name_missing"],
        "street_number": ["street_number_is_postcode"],
        "region": ["region_required"],
    }


def test_a_region_of_another_country_is_its_own_code():
    errors = address_rules.address_errors(
        country=_country(),
        street="Main",
        street_number="1",
        zipcode="12345",
        region=SimpleNamespace(country_id="CY"),
    )

    assert _codes(errors) == {"region": ["region_country_mismatch"]}
