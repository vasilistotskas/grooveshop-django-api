"""The store's shipping zones on the API: the region pickers list the
regions it delivers to, the options and the quote price a region by its
zone, and an order or a saved address where no home delivery reaches is
refused with a code (``shipping.zones``)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from country.factories import CountryFactory
from order.models.order import Order
from region.factories import RegionFactory
from shipping.factories import ShippingRateFactory, ShippingZoneFactory
from shipping.models import ShippingProvider
from tests.integration.order.test_checkout_quote import (
    _checkout,
    _choices,
    _post_order,
    _post_quote,
)
from tests.utils.checkout import UNQUOTED
from user.factories.account import UserAccountFactory
from user.factories.address import UserAddressFactory

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("checkout_legal_documents"),
]

ISLANDS = Decimal("7.50")


def _zone_regions(country):
    """Three regions of ``country`` (whose whole-country zone already has
    a rate): the mainland, the islands with their own 7.50 rate, and Crete,
    a region zone with no rate, where the store does not deliver."""
    mainland, islands, crete = (RegionFactory(country=country) for _ in "abc")
    flat_rate = ShippingProvider.objects.get(code="flat_rate")
    ShippingRateFactory(
        provider=flat_rate,
        zone=ShippingZoneFactory(country=country, regions=[islands]),
        price=ISLANDS,
        free_shipping_threshold=None,
    )
    ShippingZoneFactory(country=country, regions=[crete])
    return mainland, islands, crete


@pytest.fixture
def store():
    checkout = _checkout()
    mainland, islands, crete = _zone_regions(checkout.country)
    checkout.mainland, checkout.islands, checkout.crete = (
        mainland,
        islands,
        crete,
    )
    return checkout


def _codes(response, field: str) -> list[str]:
    return [error["code"] for error in response.json()["fields"][field]]


@pytest.fixture
def as_tenant_host(bind_tenant_and_schema):
    """On a store's own host, where ``shippable`` reads its zones; the
    suite otherwise runs as the platform host (``public``). Bound as a
    tenant, so a ``schema_context`` the request enters restores it."""
    bind_tenant_and_schema(SimpleNamespace(schema_name="webside"))


@pytest.mark.usefixtures("as_tenant_host")
class TestRegionPickers:
    def test_the_list_keeps_the_regions_the_store_delivers_to(
        self, store, documented
    ):
        response = APIClient().get(
            reverse("region-list"),
            {"country": store.country.alpha_2, "shippable": "true"},
        )

        assert response.status_code == 200, response.content
        documented(response, "/api/v1/region", "get")
        alphas = {row["alpha"] for row in response.json()["results"]}
        assert alphas == {store.mainland.alpha, store.islands.alpha}

    def test_false_lists_the_others(self, store):
        response = APIClient().get(
            reverse("region-list"),
            {"country": store.country.alpha_2, "shippable": "false"},
        )

        alphas = {row["alpha"] for row in response.json()["results"]}
        assert alphas == {store.crete.alpha}

    def test_without_the_parameter_every_region_is_listed(self, store):
        response = APIClient().get(
            reverse("region-list"), {"country": store.country.alpha_2}
        )

        assert response.json()["count"] == 3

    def test_the_regions_of_a_country_keep_the_served_ones(
        self, store, documented
    ):
        url = reverse(
            "region-get-regions-by-country-alpha-2",
            kwargs={"pk": store.country.alpha_2},
        )

        response = APIClient().get(url, {"shippable": "true"})
        unfiltered = APIClient().get(url)

        assert response.status_code == 200, response.content
        documented(
            response,
            "/api/v1/region/{alpha}/get_regions_by_country_alpha_2",
            "get",
        )
        assert {row["alpha"] for row in response.json()} == {
            store.mainland.alpha,
            store.islands.alpha,
        }
        assert len(unfiltered.json()) == 3


def test_the_platform_host_lists_no_region_as_shippable(store):
    response = APIClient().get(
        reverse("region-list"),
        {"country": store.country.alpha_2, "shippable": "true"},
    )

    assert response.json()["count"] == 0


class TestOptions:
    def _prices(self, store, **query) -> dict[str, Decimal]:
        response = APIClient().get(
            reverse("shipping-options"),
            {"country_code": store.country.alpha_2, **query},
        )
        assert response.status_code == 200, response.content
        return {
            row["providerCode"]: Decimal(str(row["price"]))
            for row in response.json()
        }

    def test_a_region_is_priced_by_its_zone(self, store):
        assert self._prices(store, region_id=store.islands.alpha) == {
            "flat_rate": ISLANDS
        }

    def test_a_region_the_store_does_not_deliver_to_lists_nothing(self, store):
        assert self._prices(store, region_id=store.crete.alpha) == {}

    def test_another_countrys_region_is_a_field_error(self, store):
        elsewhere = RegionFactory(country=CountryFactory(num_regions=0))

        response = APIClient().get(
            reverse("shipping-options"),
            {
                "country_code": store.country.alpha_2,
                "region_id": elsewhere.alpha,
            },
        )

        assert response.status_code == 400, response.content
        assert _codes(response, "regionId") == ["region_country_mismatch"]


class TestCheckout:
    def test_the_quote_charges_the_islands_rate(self, store):
        response = _post_quote(
            store, **_choices(store), region_id=store.islands.alpha
        )

        assert response.status_code == 200, response.content
        assert Decimal(str(response.json()["shipping"]["price"])) == ISLANDS

    def test_the_quote_refuses_a_delivery_that_skips_the_region(
        self, store, documented
    ):
        response = _post_quote(
            store,
            **_choices(store),
            shipping_provider_code="flat_rate",
            region_id=store.crete.alpha,
        )

        assert response.status_code == 400, response.content
        documented(response, "/api/v1/cart/quote", "post")
        assert response.json()["code"] == "shipping_region_unavailable"

    def test_an_order_to_a_region_not_served_is_refused_on_the_region(
        self, store, documented
    ):
        response = _post_order(
            store, region_id=store.crete.alpha, expected_total=UNQUOTED
        )

        assert response.status_code == 400, response.content
        documented(response, "/api/v1/order", "post")
        assert _codes(response, "regionId") == ["region_not_served"]
        assert not Order.objects.exists()

    def test_an_order_to_the_islands_is_charged_their_rate(self, store):
        response = _post_order(store, region_id=store.islands.alpha)

        assert response.status_code == 201, response.content
        order = Order.objects.get()
        assert order.shipping_price.amount == ISLANDS


class TestSavedAddresses:
    @pytest.fixture
    def customer(self, store):
        user = UserAccountFactory(num_addresses=0)
        client = APIClient()
        client.force_authenticate(user=user)
        return user, client

    def _body(self, store, region) -> dict:
        return {
            "title": "Home",
            "first_name": "Eleni",
            "last_name": "Georgiou",
            "street": "Akadimias",
            "street_number": "3",
            "city": "Athens",
            "zipcode": "10671",
            "phone": "2101234567",
            "country": store.country.alpha_2,
            "region": region.alpha,
            "is_main": False,
        }

    def test_an_address_where_the_store_delivers_is_saved(
        self, store, customer
    ):
        _user, client = customer

        response = client.post(
            reverse("user-address-list"),
            self._body(store, store.islands),
            format="json",
        )

        assert response.status_code == 201, response.content
        assert response.json()["shippable"] is True

    def test_an_address_in_a_region_not_served_is_refused(
        self, store, customer, documented
    ):
        _user, client = customer

        response = client.post(
            reverse("user-address-list"),
            self._body(store, store.crete),
            format="json",
        )

        assert response.status_code == 400, response.content
        documented(response, "/api/v1/user/address", "post")
        assert _codes(response, "region") == ["region_not_served"]

    def test_an_address_in_a_country_not_served_is_refused(self, customer):
        _user, client = customer
        country = CountryFactory(num_regions=0, postal_code_pattern="")

        response = client.post(
            reverse("user-address-list"),
            {
                "title": "Home",
                "first_name": "Eleni",
                "last_name": "Georgiou",
                "street": "Main Street",
                "street_number": "3",
                "city": "Town",
                "zipcode": "1000",
                "phone": "2101234567",
                "country": country.alpha_2,
                "is_main": False,
            },
            format="json",
        )

        assert response.status_code == 400, response.content
        assert _codes(response, "country") == ["country_not_served"]

    def test_a_kept_address_no_longer_served_reads_unshippable(
        self, store, customer, documented
    ):
        user, client = customer
        # Titles of their own: the factory gets-or-creates on the title.
        UserAddressFactory(
            user=user, title="Old", country=store.country, region=store.crete
        )
        UserAddressFactory(
            user=user,
            title="New",
            country=store.country,
            region=store.mainland,
        )

        response = client.get(reverse("user-address-list"))

        assert response.status_code == 200, response.content
        documented(response, "/api/v1/user/address", "get")
        by_region = {
            row["region"]: row["shippable"]
            for row in response.json()["results"]
        }
        assert by_region == {
            store.crete.alpha: False,
            store.mainland.alpha: True,
        }

    def test_a_kept_address_can_still_be_renamed(self, store, customer):
        user, client = customer
        address = UserAddressFactory(
            user=user, country=store.country, region=store.crete
        )

        response = client.patch(
            reverse("user-address-detail", kwargs={"pk": address.pk}),
            {"title": "Old home"},
            format="json",
        )

        assert response.status_code == 200, response.content
        assert response.json()["shippable"] is False
