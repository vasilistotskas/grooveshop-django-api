"""Greek ΑΦΜ checksum semantics.

The checksum is the only guard between checkout and an AADE rejection
at myDATA-submit time — a format-valid but checksum-invalid ΑΦΜ used to
pass the serializer and die at the worker.
"""

import pytest
from django.core.exceptions import ValidationError

from b2b.validators import is_valid_greek_vat, validate_greek_vat


class TestIsValidGreekVat:
    @pytest.mark.parametrize(
        "value",
        [
            "123456783",  # synthetic, checksum-valid
            "094014201",  # real-world format (OTE), checksum-valid
            "090000045",
        ],
    )
    def test_valid_numbers(self, value):
        assert is_valid_greek_vat(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "123456789",  # bad check digit
            "094014200",  # off by one
            "12345678",  # too short
            "1234567890",  # too long
            "12345678A",  # non-digit
            "",
            "000000000",  # degenerate all-zero (checksum trivially passes)
        ],
    )
    def test_invalid_numbers(self, value):
        assert is_valid_greek_vat(value) is False

    def test_whitespace_is_tolerated(self):
        assert is_valid_greek_vat(" 123456783 ") is True

    def test_prefix_is_not_tolerated(self):
        # Normalisation (EL/GR stripping) is the serializer's job — the
        # checksum expects the bare 9-digit form.
        assert is_valid_greek_vat("EL123456783") is False


class TestValidateGreekVat:
    def test_raises_for_invalid(self):
        with pytest.raises(ValidationError):
            validate_greek_vat("123456789")

    def test_passes_for_valid(self):
        validate_greek_vat("123456783")
