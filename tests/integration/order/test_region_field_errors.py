"""A ``regionId`` the store has no such region for, or one of another
country, is a field error on ``regionId``: on order creation and on the
quote, never a foreign key the database refuses. A pickup delivery reads
no region at all (``ShippingChoice.of``)."""

from __future__ import annotations

import pytest

from country.factories import CountryFactory
from order.models.order import Order
from region.factories import RegionFactory
from tests.integration.order.test_checkout_quote import (
    QUOTE_PATH,
    _checkout,
    _choices,
    _post_order,
    _post_quote,
)
from tests.utils.checkout import UNQUOTED

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("checkout_legal_documents"),
]


def _region_code(response) -> str:
    return response.json()["fields"]["regionId"][0]["code"]


@pytest.mark.parametrize("with_regions", [False, True])
def test_an_order_with_an_unknown_region_is_a_field_error(
    with_regions, documented
):
    checkout = _checkout()
    if with_regions:
        RegionFactory(country=checkout.country)

    response = _post_order(checkout, region_id="XX", expected_total=UNQUOTED)

    assert response.status_code == 400, response.content
    documented(response, "/api/v1/order", "post")
    assert _region_code(response) == "region_invalid"
    assert not Order.objects.exists()


def test_an_order_with_another_countrys_region_is_a_field_error():
    checkout = _checkout()
    elsewhere = RegionFactory(country=CountryFactory())

    response = _post_order(
        checkout, region_id=elsewhere.alpha, expected_total=UNQUOTED
    )

    assert response.status_code == 400, response.content
    assert _region_code(response) == "region_country_mismatch"


def test_a_missing_region_is_reported_on_region_id():
    checkout = _checkout()
    RegionFactory(country=checkout.country)

    response = _post_order(checkout, expected_total=UNQUOTED)

    assert response.status_code == 400, response.content
    assert _region_code(response) == "region_required"


def test_a_quote_with_an_unknown_region_is_a_field_error(documented):
    checkout = _checkout()

    response = _post_quote(checkout, **_choices(checkout), region_id="XX")

    assert response.status_code == 400, response.content
    documented(response, QUOTE_PATH, "post")
    assert _region_code(response) == "region_invalid"


def test_a_pickup_quote_reads_no_region():
    checkout = _checkout()

    response = _post_quote(
        checkout,
        country_id=checkout.country.alpha_2,
        shipping_kind="pickup_point",
        shipping_provider_code="boxnow",
        region_id="XX",
    )

    assert "regionId" not in response.json().get("fields", {})
