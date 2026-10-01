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
from region.models import Region

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
# Unsaved is enough — ``region.country_id`` is a plain attribute read,
# never a query, and every GREECE-based test below only needs a region
# that genuinely belongs to GR to stay error-free now that a country
# with any seeded Region rows (GR has 12, from
# ``region/migrations/0009_seed_default_regions.py``) requires one.
ATTICA = Region(alpha="GR-A1", country_id="GR")


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
    # A country-with-regions check (below) is a real query
    # (``country.regions.exists()``) whenever ``region`` is omitted —
    # every test in this class needs DB access now, even the ones
    # whose own assertions have nothing to do with regions.
    pytestmark = pytest.mark.django_db

    def test_order_316_is_rejected_on_all_three_fields(self):
        errors = address_errors(
            country=GREECE,
            street="1",
            street_number="70300",
            zipcode="ΑΒΓΔ",
            region=ATTICA,
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
                region=ATTICA,
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
            region=ATTICA,
        )
        assert "street_number" not in errors

    def test_a_street_number_that_is_a_postcode_is_flagged(self):
        errors = address_errors(
            country=GREECE,
            street="Εγνατίας",
            street_number="703 00",
            zipcode="54622",
            region=ATTICA,
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

    def test_a_region_is_required_when_the_country_has_any(self):
        """GR has 12 real seeded districts — omitting one is now a
        genuine error, not silently accepted."""
        errors = address_errors(
            country=GREECE,
            street="Εγνατίας",
            street_number="12",
            zipcode="546 22",
        )
        assert set(errors) == {"region"}

    def test_no_region_required_when_the_country_has_none(self):
        """``NO_FORMAT`` ("ZZ") is not a real, seeded country — it has
        no Region rows, so omitting one is fine."""
        errors = address_errors(
            country=NO_FORMAT,
            street="Main Street",
            street_number="1",
            zipcode="whatever",
        )
        assert errors == {}

    def test_a_region_belonging_to_a_different_country_is_rejected(self):
        cyprus_region = Region(alpha="CY-01", country_id="CY")
        errors = address_errors(
            country=GREECE,
            street="Εγνατίας",
            street_number="12",
            zipcode="546 22",
            region=cyprus_region,
        )
        assert set(errors) == {"region"}


class TestValidatePostalCodePattern:
    """The storefront applies the same pattern as a JavaScript ``RegExp``
    (``shared/utils/postalCode.ts``, no ``u`` flag), so a stored pattern
    must mean the same in both engines. Python accepts syntax JavaScript
    rejects or reads differently; the validator admits only the portable
    subset."""

    def test_accepts_a_compilable_pattern(self):
        validate_postal_code_pattern(r"\d{3} ?\d{2}")

    @pytest.mark.parametrize(
        ("alpha_2", "fmt"), sorted(SEED.GOOGLE_POSTAL_FORMATS.items())
    )
    def test_accepts_every_seeded_pattern(self, alpha_2, fmt):
        validate_postal_code_pattern(fmt[0])

    @pytest.mark.parametrize(
        "pattern",
        [
            r"[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}",
            r"(?:AI-)?2640",
            r"^\d{5}$",
            r"\d{4}(?:-\d{3})?|\d{2}\.\d{3}",
            r"[^0]\d{4}",
            r"\d{2,}",
            r"(\d)\1\d",
            r"\d{3}(?=\d)\d",
            r"(?<=K)\d{2}",
        ],
    )
    def test_accepts_portable_syntax(self, pattern):
        validate_postal_code_pattern(pattern)

    @pytest.mark.parametrize(
        ("pattern", "why"),
        [
            ("(", "does not compile anywhere"),
            (r"(?P<area>\d{2})\d{3}", "a Python-only named group"),
            (r"(?<area>\d{2})\d{3}", "a named group Python reads differently"),
            (r"(?i)[a-z]\d", "an inline flag JavaScript rejects"),
            (r"\A\d{5}\Z", "string anchors JavaScript reads as letters"),
            (r"\d{,3}", "an open lower bound JavaScript reads literally"),
            (r"\d++", "a possessive quantifier"),
            (r"(?>\d)\d", "an atomic group"),
            (r"(?#area)\d{5}", "a Python comment group"),
            (r"[[:digit:]]{5}", "a POSIX class"),
            (r"\N{DIGIT ZERO}\d", "a named Unicode escape"),
            (r"\d{5", "an unclosed brace JavaScript reads literally"),
        ],
    )
    def test_rejects_syntax_the_storefront_reads_differently(
        self, pattern, why
    ):
        with pytest.raises(ValidationError):
            validate_postal_code_pattern(pattern)
