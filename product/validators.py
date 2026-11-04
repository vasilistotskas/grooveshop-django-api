from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

GTIN_LENGTHS = (8, 12, 13, 14)


def gtin_check_digit(body: str) -> int:
    """The GS1 check digit for the digits that precede it.

    Counting from the digit next to the check digit, the weights
    alternate 3, 1, 3, 1 ...; the check digit tops the weighted sum up to
    the next multiple of ten. The rule is the same for GTIN-8, -12, -13
    and -14.
    """
    total = sum(
        int(digit) * (3 if position % 2 == 0 else 1)
        for position, digit in enumerate(reversed(body))
    )
    return (10 - total % 10) % 10


def validate_gtin(value: str) -> None:
    """Accept an empty value or a GS1 GTIN with a correct check digit."""
    if not value:
        return
    if not (value.isascii() and value.isdigit()):
        raise ValidationError(
            _("A GTIN contains digits only."), code="gtin_not_digits"
        )
    if len(value) not in GTIN_LENGTHS:
        raise ValidationError(
            _("A GTIN has 8, 12, 13 or 14 digits."), code="gtin_bad_length"
        )
    if gtin_check_digit(value[:-1]) != int(value[-1]):
        raise ValidationError(
            _("The last digit of this GTIN does not match the others."),
            code="gtin_bad_check_digit",
        )
