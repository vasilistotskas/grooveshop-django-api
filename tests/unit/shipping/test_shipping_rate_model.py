"""Unit tests for the ``ShippingRate`` model.

Single source of truth for a store's shipping price, availability and
weight cap — see ``docs/order-system.md``. ``ShippingRateFactory``
requires an explicit ``country`` (see its docstring); these tests use
``CountryFactory`` for a country that has nothing to do with the GR
row the shipping migrations seed.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from djmoney.money import Money

from country.factories import CountryFactory
from shipping.enum import ShippingKind
from shipping.factories import ShippingProviderFactory, ShippingRateFactory
from shipping.models import ShippingRate

pytestmark = pytest.mark.django_db


def test_str_is_provider_country_kind():
    provider = ShippingProviderFactory(code="rate_str_test")
    country = CountryFactory()
    rate = ShippingRateFactory(provider=provider, country=country)
    assert str(rate) == f"rate_str_test/{country.alpha_2}/home_delivery"


def test_unique_together_provider_country_kind():
    provider = ShippingProviderFactory(code="rate_unique_test")
    country = CountryFactory()
    ShippingRateFactory(
        provider=provider, country=country, kind=ShippingKind.HOME_DELIVERY
    )
    with transaction.atomic(), pytest.raises(IntegrityError):
        ShippingRate.objects.create(
            provider=provider,
            country=country,
            kind=ShippingKind.HOME_DELIVERY,
            price=Money("5.00", "EUR"),
        )


def test_same_provider_and_country_different_kind_is_allowed():
    provider = ShippingProviderFactory(
        code="rate_two_kinds_test",
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
    assert ShippingRate.objects.filter(provider=provider).count() == 2


def test_clean_rejects_a_kind_the_provider_does_not_support():
    provider = ShippingProviderFactory(
        code="rate_kind_mismatch_test",
        supports_home_delivery=True,
        supports_pickup_point=False,
    )
    country = CountryFactory()
    rate = ShippingRate(
        provider=provider,
        country=country,
        kind=ShippingKind.PICKUP_POINT,
        price=Money("3.00", "EUR"),
    )
    with pytest.raises(ValidationError) as exc_info:
        rate.clean()
    assert "kind" in exc_info.value.message_dict


def test_clean_rejects_negative_price():
    provider = ShippingProviderFactory(code="rate_negative_price_test")
    country = CountryFactory()
    rate = ShippingRate(
        provider=provider,
        country=country,
        kind=ShippingKind.HOME_DELIVERY,
        price=Money("-1.00", "EUR"),
    )
    with pytest.raises(ValidationError) as exc_info:
        rate.clean()
    assert "price" in exc_info.value.message_dict


def test_clean_rejects_a_currency_that_does_not_match_the_store():
    """Default currency is EUR everywhere in this project; a USD rate
    row is a misconfiguration, not a multi-currency store."""
    provider = ShippingProviderFactory(code="rate_currency_test")
    country = CountryFactory()
    rate = ShippingRate(
        provider=provider,
        country=country,
        kind=ShippingKind.HOME_DELIVERY,
        price=Money("3.00", "USD"),
    )
    with pytest.raises(ValidationError) as exc_info:
        rate.clean()
    assert "price" in exc_info.value.message_dict


def test_clean_passes_for_a_well_formed_rate():
    provider = ShippingProviderFactory(code="rate_valid_test")
    country = CountryFactory()
    rate = ShippingRate(
        provider=provider,
        country=country,
        kind=ShippingKind.HOME_DELIVERY,
        price=Money("3.00", "EUR"),
        free_shipping_threshold=Money("50.00", "EUR"),
    )
    rate.clean()  # must not raise


def test_free_shipping_threshold_defaults_to_null():
    """Blank means this combination is never free — never €0."""
    provider = ShippingProviderFactory(code="rate_threshold_null_test")
    country = CountryFactory()
    rate = ShippingRateFactory(
        provider=provider, country=country, free_shipping_threshold=None
    )
    assert rate.free_shipping_threshold is None


def test_max_weight_grams_defaults_to_null_meaning_no_cap():
    provider = ShippingProviderFactory(code="rate_no_cap_test")
    country = CountryFactory()
    rate = ShippingRateFactory(
        provider=provider, country=country, max_weight_grams=None
    )
    assert rate.max_weight_grams is None


def test_effective_price_returns_the_decimal_amount():
    provider = ShippingProviderFactory(code="rate_effective_price_test")
    country = CountryFactory()
    rate = ShippingRateFactory(
        provider=provider, country=country, price=Decimal("4.20")
    )
    assert rate.effective_price == Decimal("4.20")


def test_history_records_a_price_change():
    provider = ShippingProviderFactory(code="rate_history_test")
    country = CountryFactory()
    rate = ShippingRateFactory(
        provider=provider, country=country, price=Decimal("3.00")
    )
    rate.price = Money("3.50", "EUR")
    rate.save(update_fields=["price"])

    history_prices = list(
        rate.history.order_by("history_date").values_list("price", flat=True)
    )
    assert history_prices == [Decimal("3.00"), Decimal("3.50")]


def test_country_is_protected_from_deletion_while_referenced():
    from django.db.models.deletion import ProtectedError

    from country.models import Country

    provider = ShippingProviderFactory(code="rate_protect_test")
    country = CountryFactory()
    ShippingRateFactory(provider=provider, country=country)

    with transaction.atomic(), pytest.raises(ProtectedError):
        Country.objects.filter(pk=country.pk).delete()
