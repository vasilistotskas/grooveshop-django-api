"""Greek ΑΦΜ (tax identification number) validation.

The checkout serializer already normalises input (strips EL/GR prefixes
and whitespace) before validating — these helpers expect the bare
9-digit form and add the checksum the format check alone cannot catch.
"""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def is_valid_greek_vat(value: str) -> bool:
    """Checksum for a normalised 9-digit Greek ΑΦΜ.

    Algorithm: each of the first 8 digits is weighted by 2^(8-i); the
    weighted sum mod 11 mod 10 must equal the 9th (check) digit.
    """
    digits = value.strip()
    if len(digits) != 9 or not digits.isdigit():
        return False
    if digits == "000000000":
        return False
    total = sum(int(digits[i]) * (2 ** (8 - i)) for i in range(8))
    return total % 11 % 10 == int(digits[8])


def validate_greek_vat(value: str) -> None:
    if not is_valid_greek_vat(value):
        raise ValidationError(
            _("Enter a valid Greek VAT number (ΑΦΜ)."),
            code="invalid_greek_vat",
        )
