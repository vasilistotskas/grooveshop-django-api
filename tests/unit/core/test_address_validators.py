"""``core.validators.address``: the delivery-address rules.

Prod order #316 (webside, ACS home delivery) was placed with street
"1", street number "70300" (a Greek postcode) and a 4-character
non-numeric postcode, and ACS rejected the voucher. Each rule here is
aimed at one of those mix-ups.
"""

from __future__ import annotations

import importlib

import pytest
from django.core.exceptions import ValidationError

from core.validators.address import (
    address_errors,
    normalize_postcode,
    postcode_matches,
    validate_postal_code_pattern,
)
from country.models import Country

pytestmark = pytest.mark.assert_english

SEED = importlib.import_module(
    "country.migrations.0011_country_postal_code_format"
)


def _country(alpha_2: str) -> Country:
    pattern, example = SEED.GOOGLE_POSTAL_FORMATS[alpha_2]
    return Country(
        alpha_2=alpha_2,
        postal_code_pattern=pattern,
        postal_code_example=example,
    )


GREECE = _country("GR")
CYPRUS = _country("CY")
UK = _country("GB")
NO_FORMAT = Country(alpha_2="ZZ")


class TestNormalizePostcode:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("70300", "70300"),
            (" 703  00 ", "703 00"),
            ("703\t00", "703 00"),
            ("sw1a 1aa", "SW1A 1AA"),
            ("   ", ""),
        ],
    )
    def test_trims_upper_cases_and_collapses_spaces(self, raw, expected):
        assert normalize_postcode(raw) == expected


class TestPostcodeMatches:
    @pytest.mark.parametrize("value", ["70300", "703 00", " 703  00 "])
    def test_accepts_greek_postcodes_with_or_without_the_space(self, value):
        assert postcode_matches(GREECE, value)

    @pytest.mark.parametrize("value", ["ΑΒΓΔ", "7030", "703000", "", "   "])
    def test_rejects_what_is_not_a_greek_postcode(self, value):
        assert not postcode_matches(GREECE, value)

    def test_cyprus_is_four_digits(self):
        assert postcode_matches(CYPRUS, "2008")
        assert not postcode_matches(CYPRUS, "20080")

    def test_uk_is_case_insensitive(self):
        assert postcode_matches(UK, "sw1a 1aa")

    def test_a_country_without_a_format_accepts_any_non_empty_value(self):
        assert postcode_matches(NO_FORMAT, "anything")
        assert not postcode_matches(NO_FORMAT, "")

    @pytest.mark.parametrize(
        ("alpha_2", "fmt"), sorted(SEED.GOOGLE_POSTAL_FORMATS.items())
    )
    def test_every_seeded_example_matches_its_own_pattern(self, alpha_2, fmt):
        """The seed and the normaliser agree for every country: Google's
        own example, as a shopper would type it, is accepted."""
        assert postcode_matches(_country(alpha_2), fmt[1])


class TestAddressErrors:
    def test_order_316_is_rejected_on_all_three_fields(self):
        errors = address_errors(
            country=GREECE, street="1", street_number="70300", zipcode="ΑΒΓΔ"
        )

        assert set(errors) == {"street", "street_number", "zipcode"}
        assert "151 24" in str(errors["zipcode"][0])

    def test_a_correct_greek_address_passes(self):
        assert (
            address_errors(
                country=GREECE,
                street="Εγνατίας",
                street_number="12Α",
                zipcode="546 22",
            )
            == {}
        )

    @pytest.mark.parametrize("street_number", ["1", "12", "154", "12-14"])
    def test_ordinary_street_numbers_pass(self, street_number):
        errors = address_errors(
            country=GREECE,
            street="Εγνατίας",
            street_number=street_number,
            zipcode="54622",
        )
        assert "street_number" not in errors

    def test_a_street_number_that_is_a_postcode_is_flagged(self):
        errors = address_errors(
            country=GREECE,
            street="Εγνατίας",
            street_number="703 00",
            zipcode="54622",
        )
        assert set(errors) == {"street_number"}

    def test_a_country_without_a_format_only_checks_the_street(self):
        errors = address_errors(
            country=NO_FORMAT,
            street="Main Street",
            street_number="70300",
            zipcode="whatever",
        )
        assert errors == {}

    def test_the_generic_message_when_the_country_has_no_example(self):
        country = Country(alpha_2="ZZ", postal_code_pattern=r"\d{5}")
        errors = address_errors(
            country=country, street="Main", street_number="1", zipcode="x"
        )
        assert str(errors["zipcode"][0]) == "Enter a valid postcode."


class TestValidatePostalCodePattern:
    def test_accepts_a_compilable_pattern(self):
        validate_postal_code_pattern(r"\d{3} ?\d{2}")

    def test_rejects_a_broken_pattern(self):
        with pytest.raises(ValidationError):
            validate_postal_code_pattern("(")
