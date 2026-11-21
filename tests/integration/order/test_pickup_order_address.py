"""A pickup-point order is delivered to the point (#642).

The buyer's own street, number, city and postcode are not needed to
deliver to a locker or a station, and a retail receipt needs no buyer
address (Law 4308/2014 art. 12 par. 2, as amended by Law 5222/2025 art.
238). So order creation does not require them for a pickup point, and
stores the point's address from the carrier's catalogue as the order's
delivery address: the one snapshot the receipt, the ACS voucher and the
order page read. Name, email and phone stay required.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse

from order.models.order import Order
from order.serializers.order import OrderCreateFromCartSerializer
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from region.factories import RegionFactory
from shipping.models import ShippingProvider
from shipping_acs.factories import AcsStationFactory
from shipping_boxnow.factories import BoxNowLockerFactory
from tests.integration.order.test_checkout_quote import _checkout
from tests.utils.shipping import enable_rate

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("checkout_legal_documents"),
]

BUYER = {
    "first_name": "Eleni",
    "last_name": "Georgiou",
    "email": "eleni@example.com",
    "phone": "+306900000002",
}
HOME = {
    "street": "Akadimias",
    "street_number": "3",
    "city": "Athens",
    "zipcode": "10671",
}


@pytest.fixture
def locker_checkout():
    """A cart, a BOX NOW rate and locker 4 in its country, and the pay way
    a locker takes."""
    with patch("shipping_boxnow.services.is_configured", return_value=True):
        ShippingProvider.objects.filter(code="boxnow").update(is_active=True)
        checkout = _checkout()
        enable_rate(
            checkout.country, provider_code="boxnow", kind="pickup_point"
        )
        checkout.pay_way = PayWayFactory(
            provider_code="boxnow_pay_on_the_go",
            settlement=PaySettlement.CARRIER_TERMINAL,
            active=True,
        )
        checkout.locker = BoxNowLockerFactory(
            external_id="4",
            address_line_1="Ermou 12",
            address_line_2="Athens",
            postal_code="105 63",
            country_code=checkout.country.alpha_2,
        )
        yield checkout


def _post_locker_order(checkout, **body):
    return checkout.client.post(
        reverse("order-list"),
        {
            **BUYER,
            "country_id": checkout.country.alpha_2,
            "pay_way_id": checkout.pay_way.id,
            "terms_accepted": True,
            "shipping_kind": "pickup_point",
            "shipping_provider_code": "boxnow",
            "boxnow_locker_id": "4",
            **body,
        },
        format="json",
        HTTP_X_CART_ID=str(checkout.cart.uuid),
    )


class TestALockerOrder:
    def test_needs_no_address_of_the_buyer(self, locker_checkout):
        response = _post_locker_order(locker_checkout)

        assert response.status_code == 201, response.content
        order = Order.objects.get()
        assert (
            order.street,
            order.street_number,
            order.city,
            order.zipcode,
        ) == ("Ermou", "12", "Athens", "105 63")
        assert (order.first_name, order.email) == ("Eleni", BUYER["email"])

    def test_keeps_the_lockers_address_not_the_buyers(self, locker_checkout):
        response = _post_locker_order(locker_checkout, **HOME)

        assert response.status_code == 201, response.content
        order = Order.objects.get()
        assert (order.street, order.street_number) == ("Ermou", "12")
        assert order.city == "Athens"

    def test_needs_no_region(self, locker_checkout):
        RegionFactory(country=locker_checkout.country)

        response = _post_locker_order(locker_checkout)

        assert response.status_code == 201, response.content

    def test_a_locker_the_catalogue_does_not_hold_is_refused(
        self, locker_checkout, documented
    ):
        response = _post_locker_order(locker_checkout, boxnow_locker_id="99")

        assert response.status_code == 400, response.content
        documented(response, "/api/v1/order", "post")
        assert response.json()["fields"]["boxnowLockerId"][0]["code"] == (
            "pickup_point_unknown"
        )
        assert not Order.objects.exists()

    def test_still_needs_the_buyers_name_email_and_phone(self, locker_checkout):
        response = _post_locker_order(
            locker_checkout, first_name="", email="", phone=""
        )

        assert response.status_code == 400, response.content
        assert {"firstName", "email", "phone"} <= set(response.json()["fields"])


def _serializer(**body) -> OrderCreateFromCartSerializer:
    country = body.pop("country")
    return OrderCreateFromCartSerializer(
        data={
            **BUYER,
            "country_id": country.alpha_2,
            "pay_way_id": 1,
            "terms_accepted": True,
            "expected_total": {"amount": 0, "currency": "EUR"},
            **body,
        }
    )


class TestAStationOrder:
    @pytest.fixture
    def acs(self):
        ShippingProvider.objects.filter(code="acs").update(is_active=True)
        country = _checkout().country
        return country, AcsStationFactory(
            external_id="ATH01",
            branch_code="12",
            address_line_1="Ιπποκράτους 33",
            city="Αθήνα",
            postal_code="10680",
            country_code=country.alpha_2,
        )

    def test_takes_the_exact_stations_address(self, acs):
        country, _station = acs
        AcsStationFactory(
            external_id="ATH01",
            branch_code="",
            address_line_1="Another branch 1",
            country_code=country.alpha_2,
        )

        serializer = _serializer(
            country=country,
            shipping_kind="pickup_point",
            shipping_provider_code="acs",
            acs_station_external_id="ATH01",
            acs_station_branch="12",
        )

        assert serializer.is_valid(), serializer.errors
        data = serializer.validated_data
        assert (
            data["street"],
            data["street_number"],
            data["city"],
            data["zipcode"],
        ) == ("Ιπποκράτους", "33", "Αθήνα", "10680")

    def test_a_line_that_does_not_split_is_kept_whole(self, acs):
        country, station = acs
        station.address_line_1 = "Πειραιώς 193, Πετράλωνα"
        station.save()

        serializer = _serializer(
            country=country,
            shipping_kind="pickup_point",
            shipping_provider_code="acs",
            acs_station_external_id="ATH01",
            acs_station_branch="12",
        )

        assert serializer.is_valid(), serializer.errors
        data = serializer.validated_data
        assert (data["street"], data["street_number"]) == (
            "Πειραιώς 193, Πετράλωνα",
            "",
        )

    def test_without_a_station_is_refused_on_the_station(self, acs):
        country, _station = acs

        serializer = _serializer(
            country=country,
            shipping_kind="pickup_point",
            shipping_provider_code="acs",
        )

        assert not serializer.is_valid()
        error = serializer.errors["acs_station_external_id"][0]
        assert error.code == "pickup_point_required"


class TestAHomeDelivery:
    def test_still_needs_the_buyers_address(self):
        country = _checkout().country

        serializer = _serializer(country=country, shipping_kind="home_delivery")

        assert not serializer.is_valid()
        assert {"street", "city", "zipcode"} <= set(serializer.errors)
        assert serializer.errors["street"][0].code == "required"

    def test_without_a_kind_is_one(self):
        country = _checkout().country

        serializer = _serializer(country=country)

        assert not serializer.is_valid()
        assert {"street", "city", "zipcode"} <= set(serializer.errors)


def test_an_invoice_to_a_locker_names_the_company_address(locker_checkout):
    with patch("order.serializers.order.feature_on", return_value=True):
        serializer = _serializer(
            country=locker_checkout.country,
            shipping_kind="pickup_point",
            shipping_provider_code="boxnow",
            boxnow_locker_id="4",
            document_type="INVOICE",
            billing_vat_id="094014201",
            billing_company_name="Acme ΑΕ",
            billing_tax_office="Α' Αθηνών",
            billing_activity="Εμπόριο",
        )

        assert not serializer.is_valid()
    assert "billing_street" in serializer.errors
