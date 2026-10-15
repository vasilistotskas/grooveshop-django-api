"""STORE_GEO_LAT / STORE_GEO_LNG validators (tenant/validators.py).

The pair is published as the store's location (the map's pin and the
LocalBusiness structured data). The storefront reads it with the same rule
(shared/utils/coordinates.ts parseDegrees), so what passes here renders
there.
"""

from __future__ import annotations

import pytest

from tenant.validators import (
    validate_latitude_setting,
    validate_longitude_setting,
)


@pytest.mark.parametrize(
    "value", ["40.565129", "-33.8688", "0", "90", "-90", " 22.1 "]
)
def test_decimal_degrees_within_range_are_a_latitude(value):
    assert validate_latitude_setting(value)


@pytest.mark.parametrize("value", ["", None])
def test_empty_means_no_location(value):
    assert validate_latitude_setting(value)
    assert validate_longitude_setting(value)


@pytest.mark.parametrize(
    "value",
    [
        "37.9oops",  # parseFloat would read 37.9 and publish it
        "40,565129",
        "1e1",
        ".5",
        "+40.5",
        "NaN",
        "91",
        "-90.0001",
        40.5,
    ],
)
def test_anything_else_is_refused(value):
    assert not validate_latitude_setting(value)


def test_a_longitude_has_its_own_range():
    assert validate_longitude_setting("179.9")
    assert validate_longitude_setting("-180")
    assert not validate_longitude_setting("181")
