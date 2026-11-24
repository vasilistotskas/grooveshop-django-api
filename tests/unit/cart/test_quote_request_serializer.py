"""``CartQuoteRequestSerializer``: the body of ``POST /api/v1/cart/quote``.

Every choice is optional (a quote may be asked before a delivery or a
pay way is chosen), but the ones given must be coherent the way the
order-create body must be: a pickup delivery names its carrier, a kind
is one the carriers know, and a region belongs to the country.
"""

from __future__ import annotations

import pytest

from cart.serializers.cart import CartQuoteRequestSerializer
from country.factories import CountryFactory
from region.factories import RegionFactory

pytestmark = pytest.mark.django_db


def test_a_quote_may_be_asked_before_any_choice():
    serializer = CartQuoteRequestSerializer(data={})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["shipping_provider_code"] is None


def test_home_delivery_needs_no_provider_code():
    """``home_delivery`` is provider-agnostic in checkout: the
    order-create body carries no ``shippingProviderCode`` for it, so the
    quote body must accept the same shape."""
    serializer = CartQuoteRequestSerializer(
        data={
            "pay_way_id": 1,
            "shipping_kind": "home_delivery",
            "country_id": "GR",
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["shipping_provider_code"] is None


def test_pickup_point_requires_a_provider_code():
    serializer = CartQuoteRequestSerializer(
        data={"shipping_kind": "pickup_point", "country_id": "GR"}
    )
    assert not serializer.is_valid()
    assert serializer.errors["shipping_provider_code"][0].code == (
        "shipping_provider_required"
    )


def test_a_blank_provider_code_counts_as_none_for_pickup_point():
    serializer = CartQuoteRequestSerializer(
        data={
            "shipping_kind": "pickup_point",
            "shipping_provider_code": "  ",
            "country_id": "GR",
        }
    )
    assert not serializer.is_valid()
    assert "shipping_provider_code" in serializer.errors


def test_an_unknown_kind_is_refused():
    serializer = CartQuoteRequestSerializer(
        data={
            "shipping_provider_code": "acs",
            "shipping_kind": "drone",
            "country_id": "GR",
        }
    )
    assert not serializer.is_valid()
    assert "shipping_kind" in serializer.errors


def test_a_full_payload_is_accepted():
    serializer = CartQuoteRequestSerializer(
        data={
            "pay_way_id": 7,
            "shipping_provider_code": "acs",
            "shipping_kind": "home_delivery",
            "country_id": "GR",
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["shipping_provider_code"] == "acs"
    assert serializer.validated_data["shipping_kind"] == "home_delivery"


def test_an_unknown_region_is_a_field_error():
    serializer = CartQuoteRequestSerializer(
        data={
            "shipping_kind": "home_delivery",
            "country_id": CountryFactory().alpha_2,
            "region_id": "NOPE",
        }
    )
    assert not serializer.is_valid()
    assert serializer.errors["region_id"][0].code == "region_invalid"


def test_a_region_of_another_country_is_a_field_error():
    region = RegionFactory(country=CountryFactory())
    serializer = CartQuoteRequestSerializer(
        data={
            "shipping_kind": "home_delivery",
            "country_id": CountryFactory().alpha_2,
            "region_id": region.alpha,
        }
    )
    assert not serializer.is_valid()
    assert serializer.errors["region_id"][0].code == "region_country_mismatch"


def test_a_pickup_delivery_does_not_read_the_region():
    """A pickup delivery is priced without one (``ShippingChoice.of``)."""
    serializer = CartQuoteRequestSerializer(
        data={
            "shipping_kind": "pickup_point",
            "shipping_provider_code": "boxnow",
            "country_id": CountryFactory().alpha_2,
            "region_id": "NOPE",
        }
    )
    assert serializer.is_valid(), serializer.errors
