from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from product.models import Product
from product.validators import gtin_check_digit, validate_gtin


@pytest.mark.parametrize(
    "value",
    [
        "",
        "4006381333931",  # EAN-13
        "96385074",  # EAN-8
        "036000291452",  # UPC-A (12)
        "10012345678902",  # GTIN-14
    ],
)
def test_a_blank_or_valid_gtin_is_accepted(value):
    validate_gtin(value)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("4006381333932", "gtin_bad_check_digit"),
        ("400638133393a", "gtin_not_digits"),
        ("4006 381333931", "gtin_not_digits"),
        ("٤٠٠٦٣٨١٣٣٣٩٣١", "gtin_not_digits"),
        ("40063813339", "gtin_bad_length"),
        ("400638133393111", "gtin_bad_length"),
        ("123", "gtin_bad_length"),
    ],
)
def test_an_invalid_gtin_is_refused_with_its_reason(value, code):
    with pytest.raises(ValidationError) as caught:
        validate_gtin(value)

    assert caught.value.code == code


def test_the_check_digit_follows_the_gs1_weights():
    assert gtin_check_digit("400638133393") == 1
    assert gtin_check_digit("9638507") == 4


def test_the_model_field_runs_the_validator():
    product = Product(gtin="4006381333932")

    with pytest.raises(ValidationError) as caught:
        product.clean_fields(
            exclude=[f.name for f in Product._meta.fields if f.name != "gtin"]
        )

    assert "gtin" in caught.value.message_dict


def test_a_blank_gtin_and_mpn_are_the_defaults():
    product = Product()

    assert product.gtin == ""
    assert product.mpn == ""
