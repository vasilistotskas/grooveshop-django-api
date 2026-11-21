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

import logging
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from django.db.backends.base.base import BaseDatabaseWrapper
from django.urls import reverse
from redis.exceptions import ConnectionError as RedisConnectionError

from order.models.order import Order
from order.serializers.order import OrderCreateFromCartSerializer
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from region.factories import RegionFactory
from shipping.models import ShippingProvider, ShippingRate
from shipping_acs import config as acs_config
from shipping_acs.factories import AcsStationFactory
from shipping_boxnow.exceptions import BoxNowAPIError
from shipping_boxnow.factories import BoxNowLockerFactory
from tests.integration.order.test_checkout_quote import _checkout
from tests.utils.checkout import UNQUOTED, quoted_body
from tests.utils.redis import private_cache
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
    """Quote the locker order, then place it at the quote's total, as a
    client does."""
    headers = {"HTTP_X_CART_ID": str(checkout.cart.uuid)}
    order = quoted_body(
        checkout.client,
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
        **headers,
    )
    return checkout.client.post(
        reverse("order-list"), order, format="json", **headers
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

    def test_keeps_none_of_the_buyers_address(self, locker_checkout):
        region = RegionFactory(country=locker_checkout.country)

        response = _post_locker_order(
            locker_checkout,
            region_id=region.alpha,
            floor="SECOND_FLOOR",
            location_type="HOME",
        )

        assert response.status_code == 201, response.content
        order = Order.objects.get()
        assert order.region_id is None
        assert (order.floor, order.location_type) == ("", "")

    def test_a_region_that_does_not_exist_is_not_kept(self, locker_checkout):
        response = _post_locker_order(locker_checkout, region_id="XX")

        assert response.status_code == 201, response.content
        assert Order.objects.get().region_id is None

    def test_a_locker_in_another_country_is_refused(self, locker_checkout):
        locker_checkout.locker.country_code = "CY"
        locker_checkout.locker.save()

        response = _post_locker_order(locker_checkout)

        assert response.status_code == 400, response.content
        assert response.json()["fields"]["boxnowLockerId"][0]["code"] == (
            "pickup_point_in_another_country"
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
            # The serializer checks the shape alone; no quote is compared.
            "expected_total": UNQUOTED,
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

    def test_a_station_in_another_country_is_refused(self, acs):
        country, station = acs
        station.country_code = "CY"
        station.save()

        with patch("shipping.services.ShippingService.active_rate") as gate:
            serializer = _serializer(
                country=country,
                shipping_kind="pickup_point",
                shipping_provider_code="acs",
                acs_station_external_id="ATH01",
                acs_station_branch="12",
            )

            assert not serializer.is_valid()
        error = serializer.errors["acs_station_external_id"][0]
        assert error.code == "pickup_point_in_another_country"
        # No resync can change a country.
        gate.assert_not_called()

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


def _destination(external_id: str, country: str) -> dict:
    """A destination as BOX NOW's ``/api/v1/destinations`` lists it."""
    return {
        "id": external_id,
        "locationType": "apm",
        "name": f"Locker {external_id}",
        "title": external_id,
        "lat": "37.9750",
        "lng": "23.7350",
        "addressLine1": "Ermou 40",
        "addressLine2": "Athens",
        "postalCode": "105 63",
        "country": country,
    }


@pytest.fixture
def resync(locker_checkout):
    """BOX NOW's client mocked at its boundary, and a cache of the test's
    own for the resync guard."""
    client = MagicMock()
    client.return_value.partner_id = "1"
    client.return_value.list_destinations.return_value = [
        _destination("4", locker_checkout.country.alpha_2)
    ]
    with (
        patch("shipping_boxnow.services.BoxNowClient", client),
        patch("django.core.cache.cache", private_cache()),
    ):
        yield client.return_value.list_destinations
        _join_resync_threads()


def _join_resync_threads() -> None:
    """Wait for every resync thread the test started, while its mocks
    and database are still there: one left running past its test makes
    its queries after the teardown and logs their refusal into the next
    test on the worker."""
    for thread in threading.enumerate():
        if thread.name == RESYNC_THREAD:
            thread.join(10)
            assert not thread.is_alive(), "a resync thread outlived its test"


# The resync runs on a thread with a connection of its own
# (``ShippingService._resync_within_deadline``), which sees only committed
# rows: these tests run outside the test's transaction.
RESYNC = pytest.mark.django_db(transaction=True)
RESYNC_THREAD = "pickup-point-resync"


@RESYNC
class TestALockerTheCatalogueDoesNotHoldYet:
    """A locker BOX NOW's widget shows before the daily sync holds it."""

    def test_is_refused_when_the_resync_does_not_list_it(
        self, locker_checkout, resync, documented
    ):
        response = _post_locker_order(locker_checkout, boxnow_locker_id="99")

        assert response.status_code == 400, response.content
        documented(response, "/api/v1/order", "post")
        assert response.json()["fields"]["boxnowLockerId"][0]["code"] == (
            "pickup_point_unknown"
        )
        resync.assert_called_once()
        assert not Order.objects.exists()

    def test_a_withdrawn_locker_is_looked_up_again(
        self, locker_checkout, resync
    ):
        """The sync keeps a withdrawn locker's row and marks it inactive;
        an order for it counts as a miss."""
        locker_checkout.locker.is_active = False
        locker_checkout.locker.save()
        resync.return_value = []

        refused = _post_locker_order(locker_checkout)

        assert refused.status_code == 400, refused.content
        assert refused.json()["fields"]["boxnowLockerId"][0]["code"] == (
            "pickup_point_unknown"
        )
        resync.assert_called_once()

    def test_a_locker_the_carrier_lists_again_is_ordered(
        self, locker_checkout, resync
    ):
        locker_checkout.locker.is_active = False
        locker_checkout.locker.save()

        response = _post_locker_order(locker_checkout)

        assert response.status_code == 201, response.content
        resync.assert_called_once()

    def test_a_resync_past_its_deadline_is_refused_and_logged(
        self, locker_checkout, resync, settings, caplog
    ):
        settings.PICKUP_POINT_RESYNC_TIMEOUT_SECONDS = 1
        resync.side_effect = lambda **_kw: time.sleep(2) or []

        started = time.monotonic()
        with caplog.at_level(logging.WARNING, logger="shipping.services"):
            response = _post_locker_order(
                locker_checkout, boxnow_locker_id="77"
            )

        assert response.status_code == 400, response.content
        assert response.json()["fields"]["boxnowLockerId"][0]["code"] == (
            "pickup_point_unknown"
        )
        assert time.monotonic() - started < 2
        assert "did not finish within 1 s" in caplog.text

    def test_a_malformed_id_never_starts_a_resync(
        self, locker_checkout, resync
    ):
        # No quote: the quote refuses the same id on its field.
        response = locker_checkout.client.post(
            reverse("order-list"),
            {
                **BUYER,
                "country_id": locker_checkout.country.alpha_2,
                "pay_way_id": locker_checkout.pay_way.id,
                "terms_accepted": True,
                "shipping_kind": "pickup_point",
                "shipping_provider_code": "boxnow",
                "boxnow_locker_id": "9" * 65,
                "expected_total": UNQUOTED,
            },
            format="json",
            HTTP_X_CART_ID=str(locker_checkout.cart.uuid),
        )

        assert response.status_code == 400, response.content
        assert "boxnowLockerId" in response.json()["fields"]
        resync.assert_not_called()

    def test_a_body_refused_for_another_field_starts_no_resync(
        self, locker_checkout, resync
    ):
        with patch("order.serializers.order.feature_on", return_value=True):
            response = _post_locker_order(
                locker_checkout,
                boxnow_locker_id="77",
                document_type="INVOICE",
                billing_vat_id="094014201",
                billing_company_name="Acme ΑΕ",
                billing_tax_office="Α' Αθηνών",
                billing_activity="Εμπόριο",
            )

        assert response.status_code == 400, response.content
        assert "billingStreet" in response.json()["fields"]
        resync.assert_not_called()

    def test_a_store_without_lockers_in_that_country_starts_no_resync(
        self, locker_checkout, resync
    ):
        ShippingRate.objects.filter(
            country=locker_checkout.country, kind="pickup_point"
        ).update(is_active=False)

        response = _serializer(
            country=locker_checkout.country,
            shipping_kind="pickup_point",
            shipping_provider_code="boxnow",
            boxnow_locker_id="77",
        )

        assert not response.is_valid()
        assert response.errors["boxnow_locker_id"][0].code == (
            "pickup_point_unknown"
        )
        resync.assert_not_called()

    def test_is_found_by_a_resync_and_ordered_at_its_address(
        self, locker_checkout, resync
    ):
        resync.return_value.append(
            _destination("77", locker_checkout.country.alpha_2)
        )

        response = _post_locker_order(locker_checkout, boxnow_locker_id="77")

        assert response.status_code == 201, response.content
        resync.assert_called_once()
        order = Order.objects.get()
        assert (order.street, order.street_number, order.city) == (
            "Ermou",
            "40",
            "Athens",
        )

    def test_is_refused_when_the_resync_does_not_hold_it_either(
        self, locker_checkout, resync
    ):
        response = _post_locker_order(locker_checkout, boxnow_locker_id="77")

        assert response.status_code == 400, response.content
        assert response.json()["fields"]["boxnowLockerId"][0]["code"] == (
            "pickup_point_unknown"
        )
        resync.assert_called_once()
        assert not Order.objects.exists()

    def test_a_second_miss_inside_the_guard_syncs_nothing(
        self, locker_checkout, resync
    ):
        first = _post_locker_order(locker_checkout, boxnow_locker_id="77")
        second = _post_locker_order(locker_checkout, boxnow_locker_id="78")

        assert first.status_code == second.status_code == 400
        resync.assert_called_once()

    def test_a_request_during_a_sync_past_its_deadline_starts_no_other(
        self, locker_checkout, resync, settings
    ):
        """The guard is taken before the thread starts: a sync the first
        request stopped waiting for still holds it."""
        settings.PICKUP_POINT_RESYNC_TIMEOUT_SECONDS = 1
        release, finished = threading.Event(), threading.Event()

        def slow(**_kw):
            try:
                release.wait(10)
                return []
            finally:
                finished.set()

        resync.side_effect = slow
        try:
            first = _post_locker_order(locker_checkout, boxnow_locker_id="77")
            second = _post_locker_order(locker_checkout, boxnow_locker_id="78")
        finally:
            release.set()
            assert finished.wait(10)

        assert first.status_code == second.status_code == 400
        resync.assert_called_once()

    def test_a_sync_failing_after_its_deadline_is_logged_and_closed(
        self, locker_checkout, resync, settings, caplog
    ):
        """Nobody reads the result of a sync past its deadline: its thread
        logs the failure itself and closes the connection it opened."""
        settings.PICKUP_POINT_RESYNC_TIMEOUT_SECONDS = 1
        release, closed = threading.Event(), threading.Event()
        close = BaseDatabaseWrapper.close

        def late_failure(**_kw):
            release.wait(10)
            raise BoxNowAPIError(503, message="down")

        def recording_close(wrapper):
            if threading.current_thread().name == RESYNC_THREAD:
                closed.set()
            return close(wrapper)

        resync.side_effect = late_failure
        with (
            patch.object(BaseDatabaseWrapper, "close", recording_close),
            caplog.at_level(logging.ERROR, logger="shipping.services"),
        ):
            response = _post_locker_order(
                locker_checkout, boxnow_locker_id="77"
            )
            release.set()
            assert closed.wait(10)

        assert response.status_code == 400, response.content
        assert "Resync of the boxnow catalogue for pickup point" in (
            caplog.text
        )
        (logged,) = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert logged.threadName == RESYNC_THREAD
        assert isinstance(logged.exc_info[1], BoxNowAPIError)

    def test_a_carrier_that_fails_the_resync_is_refused_and_logged(
        self, locker_checkout, resync, caplog
    ):
        resync.side_effect = BoxNowAPIError(503, message="down")

        with caplog.at_level(logging.ERROR, logger="shipping.services"):
            response = _post_locker_order(
                locker_checkout, boxnow_locker_id="77"
            )

        assert response.status_code == 400, response.content
        assert response.json()["fields"]["boxnowLockerId"][0]["code"] == (
            "pickup_point_unknown"
        )
        assert "Resync of the boxnow catalogue" in caplog.text
        assert not Order.objects.exists()

    def test_a_cache_outage_refuses_without_a_resync(
        self, locker_checkout, resync, caplog
    ):
        """``CustomCache.add`` raises while Redis is down; without its
        guard no resync runs, and the order is the field error, never a
        500."""
        with (
            patch(
                "django.core.cache.cache.add",
                side_effect=RedisConnectionError("down"),
            ),
            caplog.at_level(logging.WARNING, logger="shipping.services"),
        ):
            response = _post_locker_order(
                locker_checkout, boxnow_locker_id="77"
            )

        assert response.status_code == 400, response.content
        assert response.json()["fields"]["boxnowLockerId"][0]["code"] == (
            "pickup_point_unknown"
        )
        resync.assert_not_called()
        assert "the cache holding its guard is unavailable" in caplog.text
        assert not Order.objects.exists()

    def test_a_known_locker_asks_the_carrier_nothing(
        self, locker_checkout, resync
    ):
        response = _post_locker_order(locker_checkout)

        assert response.status_code == 201, response.content
        resync.assert_not_called()


@RESYNC
class TestAStationTheCatalogueDoesNotHoldYet:
    def test_is_found_by_a_resync_of_its_country(self):
        ShippingProvider.objects.filter(code="acs").update(is_active=True)
        country = _checkout().country
        enable_rate(country, provider_code="acs", kind="pickup_point")
        # The store offers ACS lockers there: the catalogue holds others
        # of the kinds the country is synced for.
        AcsStationFactory(
            country_code=country.alpha_2,
            shop_kind=acs_config.shop_kinds_for_country(country.alpha_2)[0],
        )
        client = MagicMock()
        # ``Acs_Stations`` rows as ACS lists them, for every kind asked.
        client.return_value.stations.return_value = [
            {
                "ACS_SHOP_STATION_ID_EN": "ATH77",
                "ACS_SHOP_BRANCH_ID": "3",
                "ACS_SHOP_STATION_DESCR": "Smartpoint Ιπποκράτους",
                "ACS_SHOP_ADDRESS": "Ιπποκράτους 33",
                "ACS_SHOP_AREA_DESCR": "Αθήνα",
                "ACS_SHOP_ZIPCODE": "10680",
            }
        ]
        with (
            patch("shipping_acs.services.AcsClient", client),
            patch("shipping_acs.config.is_configured", return_value=True),
            patch("django.core.cache.cache", private_cache()),
        ):
            serializer = _serializer(
                country=country,
                shipping_kind="pickup_point",
                shipping_provider_code="acs",
                acs_station_external_id="ATH77",
                acs_station_branch="3",
            )

            assert serializer.is_valid(), serializer.errors
        client.return_value.stations.assert_called()
        assert {
            call.kwargs["country"]
            for call in client.return_value.stations.call_args_list
        } == {country.alpha_2}
        data = serializer.validated_data
        assert (data["street"], data["street_number"], data["city"]) == (
            "Ιπποκράτους",
            "33",
            "Αθήνα",
        )
