"""``ShippingRate`` delivery-day bounds and admin/option exposure."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from country.factories import CountryFactory
from shipping.factories import ShippingRateFactory
from shipping.models import ShippingProvider, ShippingRate
from shipping.services import ShippingService
from tenant.validators import validate_dispatch_cutoff_setting

pytestmark = pytest.mark.django_db


def _rate(**kwargs):
    ShippingProvider.objects.filter(code="flat_rate").update(is_active=True)
    return ShippingRateFactory.build(
        provider=ShippingProvider.objects.get(code="flat_rate"),
        country=CountryFactory(),
        **kwargs,
    )


def test_unset_bounds_are_valid_and_null():
    rate = _rate()
    rate.full_clean(exclude=["country", "provider"])
    assert rate.delivery_days_min is None
    assert rate.delivery_days_max is None


def test_both_bounds_set_is_valid():
    _rate(delivery_days_min=1, delivery_days_max=3).clean()


@pytest.mark.parametrize(
    ("low", "high", "field"),
    [
        (1, None, "delivery_days_max"),
        (None, 3, "delivery_days_min"),
        (4, 2, "delivery_days_max"),
    ],
)
def test_incomplete_or_inverted_bounds_are_rejected(low, high, field):
    with pytest.raises(ValidationError) as exc:
        _rate(delivery_days_min=low, delivery_days_max=high).clean()
    assert field in exc.value.message_dict


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("15:00", True),
        ("00:00", True),
        ("", True),
        ("24:00", False),
        ("9:00", False),
        ("15:60", False),
        ("soon", False),
        (1500, False),
    ],
)
def test_dispatch_cutoff_validator(value, ok):
    assert validate_dispatch_cutoff_setting(value) is ok


def _boxnow_option():
    ShippingProvider.objects.filter(code="boxnow").update(is_active=True)
    (option,) = [
        o
        for o in ShippingService.available_options(country_code="GR")
        if o["provider_code"] == "boxnow"
    ]
    return option


def test_options_expose_the_rates_days(boxnow_configured_tenant):
    ShippingRate.objects.filter(
        provider__code="boxnow", country_id="GR"
    ).update(delivery_days_min=1, delivery_days_max=3)
    option = _boxnow_option()
    assert option["delivery_days_min"] == 1
    assert option["delivery_days_max"] == 3


def test_options_days_are_null_when_the_rate_has_no_estimate(
    boxnow_configured_tenant,
):
    option = _boxnow_option()
    assert option["delivery_days_min"] is None
    assert option["delivery_days_max"] is None
