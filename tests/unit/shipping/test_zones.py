"""Shipping zones: how a destination resolves to the rates that apply to it
(``shipping.zones``), and the service gates that read it."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import ProtectedError
from django.test.utils import CaptureQueriesContext
from djmoney.money import Money

from country.factories import CountryFactory
from product.factories import ProductFactory
from region.factories import RegionFactory
from region.models import Region
from shipping.admin import ShippingZoneAdminForm
from shipping.enum import ShippingKind
from shipping.exceptions import (
    ShippingRegionUnavailableError,
    ShippingUnavailableError,
)
from shipping.factories import (
    ShippingProviderFactory,
    ShippingRateFactory,
    ShippingZoneFactory,
    whole_country_zone,
)
from shipping.interfaces import get_provider
from shipping.models import (
    ShippingProvider,
    ShippingRate,
    ShippingZoneRegion,
)
from shipping.services import ShippingService
from shipping.zones import (
    HomeCoverage,
    StandingDestination,
    chargeable,
    destination_errors,
    destination_rates,
    offered,
    rate_kind_errors,
    standing_destinations,
    zone_errors,
    zone_for,
)
from tests.utils.shipping import enable_rate, registered_carrier
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db

MAINLAND = Decimal("3.00")
ISLANDS = Decimal("7.50")


@pytest.fixture
def greece_like():
    """A country with three regions: the whole country ships home at
    3.00, the islands region zone at 7.50, and Crete (a region zone with
    no rate) not at all."""
    country = CountryFactory(num_regions=0)
    mainland, islands, crete = (
        RegionFactory(country=country) for _ in range(3)
    )
    base = enable_rate(country, price=MAINLAND, free_shipping_threshold=None)
    flat_rate = ShippingProvider.objects.get(code="flat_rate")
    islands_zone = ShippingZoneFactory(country=country, regions=[islands])
    ShippingRateFactory(
        provider=flat_rate,
        zone=islands_zone,
        price=ISLANDS,
        free_shipping_threshold=None,
    )
    crete_zone = ShippingZoneFactory(country=country, regions=[crete])
    return {
        "country": country,
        "mainland": mainland,
        "islands": islands,
        "crete": crete,
        "base": base.zone,
        "islands_zone": islands_zone,
        "crete_zone": crete_zone,
    }


def _options_prices(country, region=None) -> dict[str, Decimal]:
    return {
        option["provider_code"]: option["price"]
        for option in ShippingService.available_options(
            country_code=country.alpha_2,
            region_id=region.alpha if region else None,
        )
    }


class TestZoneFor:
    def test_a_region_a_region_zone_holds_resolves_to_it(self, greece_like):
        assert (
            zone_for(greece_like["country"].alpha_2, greece_like["islands"].pk)
            == greece_like["islands_zone"]
        )

    def test_any_other_region_resolves_to_the_whole_country_zone(
        self, greece_like
    ):
        assert (
            zone_for(greece_like["country"].alpha_2, greece_like["mainland"].pk)
            == greece_like["base"]
        )

    def test_no_region_resolves_to_the_whole_country_zone(self, greece_like):
        assert zone_for(greece_like["country"].alpha_2) == greece_like["base"]

    def test_a_country_without_zones_resolves_to_none(self):
        assert zone_for(CountryFactory(num_regions=0).alpha_2) is None


class TestDestinationRates:
    def test_a_pickup_rate_of_the_whole_country_zone_applies_everywhere(
        self, greece_like
    ):
        """A pickup point has no region: it is priced by the country's
        whole-country zone, wherever the shopper lives."""
        lockers = ShippingProviderFactory(
            code="zone_lockers",
            supports_home_delivery=False,
            supports_pickup_point=True,
        )
        pickup = ShippingRateFactory(
            provider=lockers,
            zone=greece_like["base"],
            kind=ShippingKind.PICKUP_POINT,
        )

        rates = destination_rates(
            greece_like["country"].alpha_2, greece_like["crete"].pk
        )

        assert list(rates) == [pickup]

    def test_a_home_delivery_takes_the_region_zones_rate(self, greece_like):
        rates = destination_rates(
            greece_like["country"].alpha_2, greece_like["islands"].pk
        )

        assert [rate.price.amount for rate in rates] == [ISLANDS]


class TestCheckoutRespectsZones:
    def test_the_islands_are_priced_by_their_zone(self, greece_like):
        assert _options_prices(
            greece_like["country"], greece_like["islands"]
        ) == {"flat_rate": ISLANDS}

    def test_the_rest_of_the_country_is_priced_by_the_whole_country_zone(
        self, greece_like
    ):
        assert _options_prices(
            greece_like["country"], greece_like["mainland"]
        ) == {"flat_rate": MAINLAND}
        assert _options_prices(greece_like["country"]) == {
            "flat_rate": MAINLAND
        }

    def test_a_region_zone_without_a_rate_offers_no_home_delivery(
        self, greece_like
    ):
        assert (
            _options_prices(greece_like["country"], greece_like["crete"]) == {}
        )

    def test_the_quote_charges_the_islands_rate(self, greece_like):
        charge = ShippingService.quote(
            provider_code="flat_rate",
            kind=ShippingKind.HOME_DELIVERY,
            country_code=greece_like["country"].alpha_2,
            region_id=greece_like["islands"].pk,
            order_value=Money(10, "EUR"),
        )

        assert charge.price.amount == ISLANDS

    def test_a_carrier_that_skips_the_region_is_refused_for_it(
        self, greece_like
    ):
        with pytest.raises(ShippingRegionUnavailableError):
            ShippingService.active_rate(
                provider_code="flat_rate",
                kind=ShippingKind.HOME_DELIVERY,
                country_code=greece_like["country"].alpha_2,
                region_id=greece_like["crete"].pk,
            )

    def test_a_carrier_the_country_has_no_rate_for_is_unavailable(self):
        country = CountryFactory(num_regions=0)
        region = RegionFactory(country=country)

        with pytest.raises(ShippingUnavailableError) as raised:
            ShippingService.active_rate(
                provider_code="flat_rate",
                kind=ShippingKind.HOME_DELIVERY,
                country_code=country.alpha_2,
                region_id=region.pk,
            )

        assert not isinstance(raised.value, ShippingRegionUnavailableError)

    def test_the_home_carrier_is_resolved_in_the_regions_zone(
        self, greece_like
    ):
        country = greece_like["country"].alpha_2

        assert (
            ShippingService.resolve_home_delivery_provider(
                country, region_id=greece_like["islands"].pk
            )
            == "flat_rate"
        )

    def test_a_region_no_carrier_reaches_names_the_countrys_carrier(
        self, greece_like
    ):
        # So the quote fails as not reaching the region, not the country.
        country = greece_like["country"].alpha_2

        assert (
            ShippingService.resolve_home_delivery_provider(
                country, region_id=greece_like["crete"].pk
            )
            == "flat_rate"
        )

    def test_a_country_no_carrier_reaches_names_none(self):
        country = CountryFactory(num_regions=0)
        region = RegionFactory(country=country)

        assert (
            ShippingService.resolve_home_delivery_provider(
                country.alpha_2, region_id=region.pk
            )
            is None
        )

    def test_a_country_served_only_by_a_region_zone_is_shippable(self):
        country = CountryFactory(num_regions=0)
        region = RegionFactory(country=country)
        zone = ShippingZoneFactory(country=country, regions=[region])
        ShippingRateFactory(
            provider=ShippingProvider.objects.get(code="flat_rate"), zone=zone
        )
        ShippingProvider.objects.filter(code="flat_rate").update(is_active=True)

        assert country.alpha_2 in ShippingService.shippable_country_codes()


class TestHomeCoverage:
    def test_a_region_is_served_by_its_own_zone_or_the_countrys(
        self, greece_like
    ):
        coverage = HomeCoverage.load()
        country = greece_like["country"].alpha_2

        assert coverage.serves(country, greece_like["mainland"].pk)
        assert coverage.serves(country, greece_like["islands"].pk)
        assert not coverage.serves(country, greece_like["crete"].pk)
        assert coverage.serves(country, None)
        assert not coverage.serves(None, None)

    def test_the_region_filter_keeps_the_served_regions(self, greece_like):
        served = set(
            Region.objects.filter(country=greece_like["country"]).filter(
                HomeCoverage.load().served_regions_q()
            )
        )

        assert served == {greece_like["mainland"], greece_like["islands"]}

    def test_an_inactive_rate_serves_nothing(self, greece_like):
        greece_like["islands_zone"].rates.update(is_active=False)

        assert not HomeCoverage.load().serves(
            greece_like["country"].alpha_2, greece_like["islands"].pk
        )

    def test_a_pickup_only_country_has_no_home_delivery(self):
        country = CountryFactory(num_regions=0)
        lockers = ShippingProviderFactory(
            code="zone_only_lockers",
            supports_home_delivery=False,
            supports_pickup_point=True,
        )
        ShippingRateFactory(
            provider=lockers,
            country=country,
            kind=ShippingKind.PICKUP_POINT,
        )

        assert not HomeCoverage.load().serves(country.alpha_2, None)

    def test_a_carrier_checkout_would_refuse_serves_nothing(self, greece_like):
        # The gate a quote puts on the carrier (its adapter's kind switch:
        # ACS without the store's credentials offers no home delivery)
        # decides coverage too, so no region is offered where checkout
        # would then find no delivery.
        country = greece_like["country"].alpha_2
        adapter = type(get_provider("flat_rate"))

        with mock.patch.object(adapter, "is_kind_enabled", return_value=False):
            coverage = HomeCoverage.load()

        assert not coverage.serves(country, greece_like["mainland"].pk)
        assert not coverage.serves(country, greece_like["islands"].pk)
        assert country not in coverage.reached

    def test_a_carrier_that_stopped_supporting_home_delivery_serves_nothing(
        self, greece_like
    ):
        ShippingProvider.objects.filter(code="flat_rate").update(
            supports_home_delivery=False
        )

        assert not HomeCoverage.load().serves(
            greece_like["country"].alpha_2, greece_like["mainland"].pk
        )

    def test_a_country_whose_carrier_checkout_refuses_is_not_shippable(
        self, greece_like
    ):
        country = greece_like["country"].alpha_2
        adapter = type(get_provider("flat_rate"))
        assert country in ShippingService.shippable_country_codes()

        with mock.patch.object(
            adapter, "kind_enabled_countries", return_value=frozenset()
        ):
            assert country not in ShippingService.shippable_country_codes()

    def test_the_error_names_the_field_to_fix(self, greece_like):
        country = greece_like["country"].alpha_2
        crete = greece_like["crete"].pk

        region_error = destination_errors(country, crete)
        country_error = destination_errors(
            CountryFactory(num_regions=0).alpha_2, None
        )

        unreached = CountryFactory(num_regions=0)
        unreached_region = destination_errors(
            unreached.alpha_2, RegionFactory(country=unreached).pk
        )

        assert region_error["region"][0].code == "region_not_served"
        assert country_error["country"][0].code == "country_not_served"
        # No home delivery anywhere in the country: the country is the
        # reason, whichever region was given.
        assert unreached_region["country"][0].code == "country_not_served"
        assert destination_errors(country, greece_like["mainland"].pk) == {}


class TestZoneRules:
    def test_a_second_whole_country_zone_is_refused(self, greece_like):
        new = ShippingZoneFactory.build(country=greece_like["country"])

        errors = zone_errors(
            zone=new, country_code=greece_like["country"].pk, regions=[]
        )

        assert "regions" in errors

    def test_the_whole_country_zone_itself_may_be_saved_again(
        self, greece_like
    ):
        assert (
            zone_errors(
                zone=greece_like["base"],
                country_code=greece_like["country"].pk,
                regions=[],
            )
            == {}
        )

    def test_a_region_of_another_country_is_refused(self, greece_like):
        elsewhere = RegionFactory(country=CountryFactory(num_regions=0))
        new = ShippingZoneFactory.build(country=greece_like["country"])

        errors = zone_errors(
            zone=new,
            country_code=greece_like["country"].pk,
            regions=[elsewhere],
        )

        assert "regions" in errors

    def test_a_region_another_zone_holds_is_refused(self, greece_like):
        new = ShippingZoneFactory.build(country=greece_like["country"])

        errors = zone_errors(
            zone=new,
            country_code=greece_like["country"].pk,
            regions=[greece_like["islands"]],
        )

        assert "regions" in errors

    def test_a_pickup_rate_on_a_region_zone_is_refused(self):
        assert rate_kind_errors([ShippingKind.PICKUP_POINT], has_regions=True)
        assert not rate_kind_errors(
            [ShippingKind.PICKUP_POINT], has_regions=False
        )
        assert not rate_kind_errors(
            [ShippingKind.HOME_DELIVERY], has_regions=True
        )


def test_the_factory_reuses_a_countrys_whole_country_zone():
    country = CountryFactory(num_regions=0)

    assert whole_country_zone(country) == whole_country_zone(country)


def test_a_zone_save_serialises_the_countrys_zones():
    # "One whole-country zone per country" has no column a constraint can
    # hold: the form takes a lock on the country's zones for the rest of
    # the admin's transaction, so a concurrent save waits and then sees
    # this one.
    country = CountryFactory(num_regions=0)
    form = ShippingZoneAdminForm(data={"name": "Q", "country": country.pk})

    with transaction.atomic():
        assert form.is_valid(), form.errors
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND pid = pg_backend_pid()"
            )
            (held,) = cursor.fetchone()

    assert held >= 1


class TestARateWithoutAZone:
    """A rate the previous release inserts during the rollout: a country
    and no zone (``0025`` keeps the column nullable for it until the
    contract release backfills it). It applies nowhere, and nothing that
    reads the store's rates fails on it."""

    @pytest.fixture
    def zoneless(self):
        served = CountryFactory(num_regions=0)
        enable_rate(served)
        # A zoned ACS rate, so the station sync has a country to sync.
        enable_rate(served, provider_code="acs", kind=ShippingKind.PICKUP_POINT)
        written_by_the_old_release = CountryFactory(num_regions=0)
        rate = enable_rate(served, provider_code="acs")
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE shipping_shippingrate "
                "SET zone_id = NULL, country_id = %s WHERE id = %s",
                [written_by_the_old_release.alpha_2, rate.pk],
            )
        return SimpleNamespace(
            rate=rate,
            served=served.alpha_2,
            country=written_by_the_old_release.alpha_2,
        )

    def test_it_is_not_chargeable(self, zoneless):
        rates = ShippingRate.objects.all()

        assert zoneless.rate.pk not in chargeable(rates).values_list(
            "pk", flat=True
        )
        assert zoneless.rate not in offered(rates)

    def test_the_stores_countries_and_coverage_leave_it_out(self, zoneless):
        shippable = ShippingService.shippable_country_codes()
        coverage = HomeCoverage.load()

        assert zoneless.served in shippable
        assert zoneless.country not in shippable
        assert zoneless.country not in coverage.reached

    def test_the_acs_station_sync_never_syncs_a_null_country(
        self, zoneless, acs_configured_tenant
    ):
        from shipping_acs.tasks import sync_acs_stations

        with mock.patch(
            "shipping_acs.services.AcsService.sync_stations",
            return_value={"upserted": 0, "deactivated": 0},
        ) as sync:
            sync_acs_stations.run()

        synced = {call.kwargs["country"] for call in sync.call_args_list}
        assert zoneless.served in synced
        assert None not in synced
        assert zoneless.country not in synced


class TestTheCountryARateCarries:
    """The expand release writes ``ShippingRate.country`` for the release
    before (``ShippingZone.rate_country_id``): a base zone's rate carries
    the country, a region zone's none, so that release prices exactly
    the rates its one-rate-per-country shape can."""

    def test_a_base_zone_rate_carries_its_country(self, greece_like):
        country = greece_like["country"]
        rate = greece_like["base"].rates.get()

        assert rate.country_id == country.alpha_2
        assert list(
            ShippingRate.objects.filter(country_id=country.alpha_2)
        ) == [rate]

    def test_a_region_zone_rate_carries_none(self, greece_like):
        assert greece_like["islands_zone"].rates.get().country_id is None

    def test_a_save_of_some_fields_still_writes_it(self, greece_like):
        rate = greece_like["base"].rates.get()
        ShippingRate.objects.filter(pk=rate.pk).update(country=None)
        rate.refresh_from_db()

        rate.save(update_fields=["is_active"])

        rate.refresh_from_db()
        assert rate.country_id == greece_like["country"].alpha_2

    def test_a_zone_that_gains_regions_clears_it(self, greece_like):
        base = greece_like["base"]

        base.regions.add(greece_like["mainland"])

        assert base.rates.get().country_id is None

    def test_a_zone_that_loses_its_regions_writes_it(self, greece_like):
        islands_zone = greece_like["islands_zone"]
        greece_like["base"].delete()

        islands_zone.regions.clear()

        assert (
            islands_zone.rates.get().country_id
            == greece_like["country"].alpha_2
        )

    def test_a_zone_cannot_lose_its_regions_beside_a_base_zone(
        self, greece_like
    ):
        # Emptied, it would be the country's second base zone, its rate
        # the base zone's duplicate under the old unique (provider,
        # country, kind): refused by the zones' rule, never an
        # IntegrityError, and nothing is written.
        islands_zone = greece_like["islands_zone"]

        with pytest.raises(ValidationError) as refused:
            islands_zone.set_regions([])

        assert set(refused.value.message_dict) == {"regions"}

        assert list(islands_zone.regions.all()) == [greece_like["islands"]]
        assert islands_zone.rates.get().country_id is None

    def test_a_region_row_written_directly_rewrites_it(self, greece_like):
        base = greece_like["base"]
        country = greece_like["country"].alpha_2

        ShippingZoneRegion.objects.create(
            zone=base, region=greece_like["mainland"]
        )
        assert base.rates.get().country_id is None

        ShippingZoneRegion.objects.filter(zone=base).delete()
        assert base.rates.get().country_id == country

    def test_a_region_a_zone_holds_cannot_be_deleted(self, greece_like):
        # So no cascade empties a zone's set under its rates.
        with pytest.raises(ProtectedError):
            greece_like["islands"].delete()

    def test_a_zone_moved_to_another_country_moves_it(self, greece_like):
        base = greece_like["base"]
        other = CountryFactory(num_regions=0)

        base.country = other
        base.save()

        assert base.rates.get().country_id == other.alpha_2


class TestARegionOnlyCountry:
    """A store that delivers to some regions of a country and has no base
    zone there: the country is shippable (the country lists name it,
    checkout may preselect it), so every read that knows no region judges
    it at those region zones, and its policy rows name the regions."""

    @pytest.fixture
    def crete_only(self):
        country = CountryFactory(num_regions=0)
        crete, _elsewhere = (RegionFactory(country=country) for _ in "ab")
        ShippingRateFactory(
            provider=ShippingProvider.objects.get(code="flat_rate"),
            zone=ShippingZoneFactory(country=country, regions=[crete]),
            price=ISLANDS,
            free_shipping_threshold=Decimal("60.00"),
        )
        ShippingProvider.objects.filter(code="flat_rate").update(is_active=True)
        return SimpleNamespace(country=country.alpha_2, crete=crete.alpha)

    def test_its_standing_destinations_are_its_region_zones(self, crete_only):
        assert standing_destinations([crete_only.country]) == {
            crete_only.country: [
                StandingDestination(
                    kinds=frozenset({ShippingKind.HOME_DELIVERY.value}),
                    region_code=crete_only.crete,
                    region_codes=(crete_only.crete,),
                )
            ]
        }

    def test_it_is_shippable(self, crete_only):
        assert crete_only.country in ShippingService.shippable_country_codes()

    def test_the_free_shipping_line_has_its_threshold(self, crete_only):
        info = ShippingService.free_shipping_info(
            country_code=crete_only.country
        )

        assert info["min_threshold"] == Decimal("60.00")

    def test_the_policy_names_its_regions(self, crete_only):
        rows = [
            row
            for row in ShippingService.shipping_policy()
            if row["country_code"] == crete_only.country
        ]

        assert [(row["price"], row["region_codes"]) for row in rows] == [
            (ISLANDS, [crete_only.crete])
        ]

    def test_a_products_estimate_is_its_regions_price(self, crete_only):
        product = ProductFactory(
            stock=10,
            price=Money(Decimal("10.00"), "EUR"),
            discount_percent=Decimal(0),
            vat=VatFactory(value=0),
            active=True,
            num_images=0,
            num_reviews=0,
        )

        with mock.patch.object(
            ShippingService,
            "initial_country_code",
            return_value=crete_only.country,
        ):
            estimate = ShippingService.product_shipping_from(product)

        assert estimate is not None
        assert estimate.price.amount == ISLANDS

    def test_a_country_with_a_base_zone_is_judged_there(self, greece_like):
        country = greece_like["country"].alpha_2

        assert standing_destinations([country]) == {
            country: [
                StandingDestination(
                    kinds=frozenset({ShippingKind.HOME_DELIVERY.value})
                )
            ]
        }
        rows = [
            row
            for row in ShippingService.shipping_policy()
            if row["country_code"] == greece_like["country"].alpha_2
        ]
        assert [(row["price"], row["region_codes"]) for row in rows] == [
            (MAINLAND, [])
        ]


class TestStandingDestinationsCountOfferedRatesOnly:
    """The same gate as the shippable countries: a base zone stands for
    the country only for what it really offers."""

    @pytest.fixture
    def regions(self):
        country = CountryFactory(num_regions=0)
        crete = RegionFactory(country=country)
        ShippingRateFactory(
            provider=ShippingProvider.objects.get(code="flat_rate"),
            zone=ShippingZoneFactory(country=country, regions=[crete]),
            price=ISLANDS,
        )
        ShippingProvider.objects.filter(code="flat_rate").update(is_active=True)
        return SimpleNamespace(country=country, crete=crete.alpha)

    def test_a_base_zone_rate_of_an_inactive_carrier_does_not_count(
        self, regions
    ):
        off = ShippingProviderFactory(code="zones_off", is_active=False)
        ShippingRateFactory(provider=off, country=regions.country)
        code = regions.country.alpha_2

        assert standing_destinations([code])[code] == [
            StandingDestination(
                kinds=frozenset({ShippingKind.HOME_DELIVERY.value}),
                region_code=regions.crete,
                region_codes=(regions.crete,),
            )
        ]

    def test_a_pickup_only_base_zone_leaves_home_delivery_to_the_regions(
        self, regions
    ):
        lockers = ShippingProviderFactory(
            code="zones_lockers",
            supports_home_delivery=False,
            supports_pickup_point=True,
        )
        ShippingRateFactory(
            provider=lockers,
            country=regions.country,
            kind=ShippingKind.PICKUP_POINT,
        )
        code = regions.country.alpha_2

        with registered_carrier(lockers.code):
            destinations = standing_destinations([code])[code]
            rows = [
                (row["kind"], row["region_codes"])
                for row in ShippingService.shipping_policy()
                if row["country_code"] == code
            ]

        assert destinations == [
            StandingDestination(
                kinds=frozenset({ShippingKind.PICKUP_POINT.value})
            ),
            StandingDestination(
                kinds=frozenset({ShippingKind.HOME_DELIVERY.value}),
                region_code=regions.crete,
                region_codes=(regions.crete,),
            ),
        ]
        assert sorted(rows) == [
            ("home_delivery", [regions.crete]),
            ("pickup_point", []),
        ]

    def test_every_country_is_read_at_once(self, regions, greece_like):
        countries = [regions.country.alpha_2, greece_like["country"].alpha_2]

        with CaptureQueriesContext(connection) as one:
            standing_destinations(countries[:1])
        with CaptureQueriesContext(connection) as both:
            standing_destinations(countries)

        assert len(both) == len(one)
