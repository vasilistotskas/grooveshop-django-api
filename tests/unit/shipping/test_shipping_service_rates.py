"""Unit tests for the rate-resolution + pricing half of ``ShippingService``.

Covers the gate every quote and every order-creation path shares:

* ``active_rate`` — resolves (or raises ``ShippingUnavailableError``)
  for every reason a combination can be unavailable: no provider code,
  inactive provider, unregistered adapter, a kind the provider doesn't
  support, ``is_kind_enabled`` False, or simply no rate row.
* ``assert_available`` — the same gate, plus the weight cap.
* ``quote`` — availability → weight cap → free-shipping threshold →
  ``live_quote`` → the rate's own price, in that order.
* ``shippable_country_codes`` / ``resolve_home_delivery_provider`` —
  the two country-aware lookups the order-creation flow and the
  Country API's ``?shippable=true`` filter share.

Exercised against the REAL seeded ``flat_rate`` provider, not a
throwaway ``ShippingProviderFactory`` row: ``active_rate`` gates on
``is_registered(provider_code)``, which only ever contains the carrier
codes actually registered at ``AppConfig.ready()`` time (``flat_rate``,
``acs``, ``boxnow``) — a made-up code can never pass it. ``flat_rate``
needs no tenant credentials (unlike ``acs``/``boxnow``), so
``is_kind_enabled`` is a plain ``is_active`` check, and it genuinely
doesn't support ``pickup_point`` — exactly the "kind mismatch" case
these tests need, with no fake data required.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from country.factories import CountryFactory
from shipping.enum import ShippingKind
from shipping.exceptions import (
    ShippingUnavailableError,
    ShippingWeightExceededError,
)
from shipping.factories import ShippingProviderFactory, ShippingRateFactory
from shipping.models import ShippingProvider
from shipping.services import ShippingService

pytestmark = pytest.mark.django_db


@pytest.fixture
def active_flat_rate() -> ShippingProvider:
    provider = ShippingProvider.objects.get(code="flat_rate")
    provider.is_active = True
    provider.save(update_fields=["is_active"])
    return provider


class TestActiveRate:
    def test_resolves_the_matching_rate(self, active_flat_rate):
        country = CountryFactory()
        rate = ShippingRateFactory(provider=active_flat_rate, country=country)

        resolved = ShippingService.active_rate(
            provider_code="flat_rate",
            kind=ShippingKind.HOME_DELIVERY.value,
            country_code=country.alpha_2,
        )
        assert resolved.pk == rate.pk

    def test_lowercase_country_code_is_normalised(self, active_flat_rate):
        country = CountryFactory()
        ShippingRateFactory(provider=active_flat_rate, country=country)

        resolved = ShippingService.active_rate(
            provider_code="flat_rate",
            kind=ShippingKind.HOME_DELIVERY.value,
            country_code=country.alpha_2.lower(),
        )
        assert resolved.country_id == country.alpha_2

    def test_raises_without_a_provider_code(self):
        country = CountryFactory()
        with pytest.raises(ShippingUnavailableError):
            ShippingService.active_rate(
                provider_code=None,
                kind=ShippingKind.HOME_DELIVERY.value,
                country_code=country.alpha_2,
            )

    def test_raises_without_a_country_code(self, active_flat_rate):
        with pytest.raises(ShippingUnavailableError):
            ShippingService.active_rate(
                provider_code="flat_rate",
                kind=ShippingKind.HOME_DELIVERY.value,
                country_code=None,
            )

    def test_raises_for_an_unregistered_provider_code(self):
        country = CountryFactory()
        with pytest.raises(ShippingUnavailableError):
            ShippingService.active_rate(
                provider_code="not_a_real_carrier",
                kind=ShippingKind.HOME_DELIVERY.value,
                country_code=country.alpha_2,
            )

    def test_raises_when_the_provider_is_inactive(self):
        """``flat_rate`` is seeded inactive by default (no ``active_
        flat_rate`` fixture here) — the same state a fresh test starts
        from."""
        country = CountryFactory()
        ShippingRateFactory(
            provider=ShippingProvider.objects.get(code="flat_rate"),
            country=country,
        )

        with pytest.raises(ShippingUnavailableError):
            ShippingService.active_rate(
                provider_code="flat_rate",
                kind=ShippingKind.HOME_DELIVERY.value,
                country_code=country.alpha_2,
            )

    def test_raises_when_the_provider_does_not_support_the_kind(
        self, active_flat_rate
    ):
        """``flat_rate`` genuinely has no locker network."""
        country = CountryFactory()

        with pytest.raises(ShippingUnavailableError):
            ShippingService.active_rate(
                provider_code="flat_rate",
                kind=ShippingKind.PICKUP_POINT.value,
                country_code=country.alpha_2,
            )

    def test_raises_when_no_rate_exists_for_the_country(self, active_flat_rate):
        """The common Cyprus case: the provider is active and supports
        the kind, but nobody added a rate for this country yet."""
        other_country = CountryFactory()
        no_rate_country = CountryFactory()
        ShippingRateFactory(provider=active_flat_rate, country=other_country)

        with pytest.raises(ShippingUnavailableError):
            ShippingService.active_rate(
                provider_code="flat_rate",
                kind=ShippingKind.HOME_DELIVERY.value,
                country_code=no_rate_country.alpha_2,
            )

    def test_raises_when_the_rate_itself_is_inactive(self, active_flat_rate):
        country = CountryFactory()
        ShippingRateFactory(
            provider=active_flat_rate, country=country, is_active=False
        )

        with pytest.raises(ShippingUnavailableError):
            ShippingService.active_rate(
                provider_code="flat_rate",
                kind=ShippingKind.HOME_DELIVERY.value,
                country_code=country.alpha_2,
            )


class TestAssertAvailable:
    def test_passes_under_the_weight_cap(self, active_flat_rate):
        country = CountryFactory()
        ShippingRateFactory(
            provider=active_flat_rate, country=country, max_weight_grams=4000
        )
        ShippingService.assert_available(
            provider_code="flat_rate",
            kind=ShippingKind.HOME_DELIVERY.value,
            country_code=country.alpha_2,
            weight_grams=3000,
        )

    def test_raises_over_the_weight_cap(self, active_flat_rate):
        country = CountryFactory()
        ShippingRateFactory(
            provider=active_flat_rate, country=country, max_weight_grams=4000
        )
        with pytest.raises(ShippingWeightExceededError):
            ShippingService.assert_available(
                provider_code="flat_rate",
                kind=ShippingKind.HOME_DELIVERY.value,
                country_code=country.alpha_2,
                weight_grams=5000,
            )

    def test_no_cap_means_any_weight_passes(self, active_flat_rate):
        country = CountryFactory()
        ShippingRateFactory(
            provider=active_flat_rate, country=country, max_weight_grams=None
        )
        ShippingService.assert_available(
            provider_code="flat_rate",
            kind=ShippingKind.HOME_DELIVERY.value,
            country_code=country.alpha_2,
            weight_grams=100_000,
        )

    def test_unavailable_still_raises_before_the_weight_check(self):
        country = CountryFactory()
        with pytest.raises(ShippingUnavailableError):
            ShippingService.assert_available(
                provider_code="not_a_real_carrier",
                kind=ShippingKind.HOME_DELIVERY.value,
                country_code=country.alpha_2,
                weight_grams=100,
            )


class TestQuote:
    def test_uses_the_rate_price_by_default(self, active_flat_rate):
        country = CountryFactory()
        ShippingRateFactory(
            provider=active_flat_rate,
            country=country,
            price=Decimal("4.20"),
            free_shipping_threshold=None,
        )
        price = ShippingService.quote(
            provider_code="flat_rate",
            kind=ShippingKind.HOME_DELIVERY.value,
            country_code=country.alpha_2,
            order_value=Money("10.00", "EUR"),
        )
        assert price == Money("4.20", "EUR")

    def test_free_above_the_threshold(self, active_flat_rate):
        country = CountryFactory()
        ShippingRateFactory(
            provider=active_flat_rate,
            country=country,
            price=Decimal("4.20"),
            free_shipping_threshold=Decimal("50.00"),
        )
        price = ShippingService.quote(
            provider_code="flat_rate",
            kind=ShippingKind.HOME_DELIVERY.value,
            country_code=country.alpha_2,
            order_value=Money("50.00", "EUR"),
        )
        assert price == Money("0", "EUR")

    def test_below_the_threshold_still_charges(self, active_flat_rate):
        country = CountryFactory()
        ShippingRateFactory(
            provider=active_flat_rate,
            country=country,
            price=Decimal("4.20"),
            free_shipping_threshold=Decimal("50.00"),
        )
        price = ShippingService.quote(
            provider_code="flat_rate",
            kind=ShippingKind.HOME_DELIVERY.value,
            country_code=country.alpha_2,
            order_value=Money("49.99", "EUR"),
        )
        assert price == Money("4.20", "EUR")

    def test_live_quote_overrides_the_rate_price(self, active_flat_rate):
        """A carrier's ``live_quote`` wins over the stored price when
        it returns a value — the ACS dynamic-pricing case. ``flat_rate``
        has no live pricing of its own; patched here purely to exercise
        the override path without standing up ACS's tenant-credential
        machinery.
        """
        from unittest.mock import patch

        country = CountryFactory()
        ShippingRateFactory(
            provider=active_flat_rate,
            country=country,
            price=Decimal("4.20"),
            free_shipping_threshold=None,
        )
        with patch(
            "shipping.carriers.flat_rate.FlatRateCarrier.live_quote",
            return_value=Decimal("7.77"),
        ):
            price = ShippingService.quote(
                provider_code="flat_rate",
                kind=ShippingKind.HOME_DELIVERY.value,
                country_code=country.alpha_2,
                order_value=Money("10.00", "EUR"),
            )
        assert price == Money("7.77", "EUR")

    def test_raises_when_unavailable(self):
        country = CountryFactory()
        with pytest.raises(ShippingUnavailableError):
            ShippingService.quote(
                provider_code="not_a_real_carrier",
                kind=ShippingKind.HOME_DELIVERY.value,
                country_code=country.alpha_2,
                order_value=Money("10.00", "EUR"),
            )

    def test_raises_over_the_weight_cap_before_pricing(self, active_flat_rate):
        country = CountryFactory()
        ShippingRateFactory(
            provider=active_flat_rate, country=country, max_weight_grams=4000
        )
        with pytest.raises(ShippingWeightExceededError):
            ShippingService.quote(
                provider_code="flat_rate",
                kind=ShippingKind.HOME_DELIVERY.value,
                country_code=country.alpha_2,
                weight_grams=5000,
                order_value=Money("10.00", "EUR"),
            )


class TestShippableCountryCodes:
    def test_includes_a_country_with_an_active_rate(self):
        provider = ShippingProviderFactory(code="rate_shippable_test")
        country = CountryFactory()
        ShippingRateFactory(provider=provider, country=country)
        assert country.alpha_2 in ShippingService.shippable_country_codes()

    def test_excludes_a_country_whose_rate_is_inactive(self):
        provider = ShippingProviderFactory(code="rate_shippable_inactive_test")
        country = CountryFactory()
        ShippingRateFactory(provider=provider, country=country, is_active=False)
        assert country.alpha_2 not in ShippingService.shippable_country_codes()

    def test_excludes_a_country_whose_provider_is_inactive(self):
        provider = ShippingProviderFactory(
            code="rate_shippable_provider_off_test", is_active=False
        )
        country = CountryFactory()
        ShippingRateFactory(provider=provider, country=country)
        assert country.alpha_2 not in ShippingService.shippable_country_codes()

    def test_ordered_by_country_sort_order(self):
        provider = ShippingProviderFactory(code="rate_shippable_order_test")
        first = CountryFactory(sort_order=1)
        second = CountryFactory(sort_order=2)
        ShippingRateFactory(provider=provider, country=second)
        ShippingRateFactory(provider=provider, country=first)

        codes = ShippingService.shippable_country_codes()
        assert codes.index(first.alpha_2) < codes.index(second.alpha_2)

    def test_both_kinds_for_the_same_provider_count_as_one_country(self):
        """Regression guard: ``ShippingRate.Meta.ordering`` interacting
        with Postgres' ``SELECT DISTINCT`` used to make a country with
        rates under two kinds count twice."""
        provider = ShippingProviderFactory(
            code="rate_shippable_two_kinds_test",
            supports_home_delivery=True,
            supports_pickup_point=True,
        )
        country = CountryFactory()
        ShippingRateFactory(
            provider=provider, country=country, kind=ShippingKind.HOME_DELIVERY
        )
        ShippingRateFactory(
            provider=provider, country=country, kind=ShippingKind.PICKUP_POINT
        )

        codes = ShippingService.shippable_country_codes()
        assert codes.count(country.alpha_2) == 1


class TestResolveHomeDeliveryProvider:
    def test_picks_the_lowest_priority_active_provider_for_the_country(self):
        country = CountryFactory()
        low_priority = ShippingProviderFactory(
            code="rate_home_low_pri_test", priority=5
        )
        high_priority = ShippingProviderFactory(
            code="rate_home_high_pri_test", priority=1
        )
        ShippingRateFactory(provider=low_priority, country=country)
        ShippingRateFactory(provider=high_priority, country=country)

        assert (
            ShippingService.resolve_home_delivery_provider(country.alpha_2)
            == high_priority.code
        )

    def test_a_capped_carrier_listed_first_does_not_hide_one_that_fits(
        self,
    ):
        country = CountryFactory()
        capped = ShippingProviderFactory(code="rate_home_capped", priority=1)
        roomy = ShippingProviderFactory(code="rate_home_roomy", priority=5)
        ShippingRateFactory(
            provider=capped, country=country, max_weight_grams=2000
        )
        ShippingRateFactory(
            provider=roomy, country=country, max_weight_grams=None
        )

        assert (
            ShippingService.resolve_home_delivery_provider(
                country.alpha_2, weight_grams=5000
            )
            == roomy.code
        )
        assert (
            ShippingService.resolve_home_delivery_provider(
                country.alpha_2, weight_grams=1500
            )
            == capped.code
        )

    def test_when_no_carrier_fits_it_still_names_the_first(self):
        """Over every cap, the first carrier is still returned, so the
        quote that follows raises the weight error (covered by the
        weight-cap tests above) rather than the misleading "not
        available for this country"."""
        country = CountryFactory()
        first = ShippingProviderFactory(code="rate_home_first", priority=1)
        second = ShippingProviderFactory(code="rate_home_second", priority=5)
        ShippingRateFactory(
            provider=first, country=country, max_weight_grams=1000
        )
        ShippingRateFactory(
            provider=second, country=country, max_weight_grams=2000
        )

        assert (
            ShippingService.resolve_home_delivery_provider(
                country.alpha_2, weight_grams=9000
            )
            == first.code
        )

    def test_ignores_a_provider_with_no_rate_for_the_country(self):
        rated_country = CountryFactory()
        unrated_country = CountryFactory()
        provider = ShippingProviderFactory(code="rate_home_no_rate_test")
        ShippingRateFactory(provider=provider, country=rated_country)

        assert (
            ShippingService.resolve_home_delivery_provider(
                unrated_country.alpha_2
            )
            is None
        )

    def test_returns_none_when_nothing_has_a_rate_for_the_country(self):
        """A random country the seeded ``flat_rate``/``acs``/``boxnow``
        rows have no GR-only rate for."""
        country = CountryFactory()
        assert (
            ShippingService.resolve_home_delivery_provider(country.alpha_2)
            is None
        )

    def test_no_country_falls_back_to_lowest_priority_regardless_of_rates(
        self, active_flat_rate
    ):
        """Without a destination yet, there is nothing more specific
        to prefer — the lowest-priority active home-delivery provider
        wins even without checking rate coverage."""
        assert (
            ShippingService.resolve_home_delivery_provider(None) == "flat_rate"
        )
