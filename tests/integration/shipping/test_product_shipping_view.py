"""``GET /api/v1/shipping/product/<id>`` and
``ShippingService.product_shipping_from``.

The product page's "shipping from X": the cheapest delivery checkout
would charge a cart holding that product alone, to the store's first
shipping country (GR: the only country the shipping migrations seed a
rate for, ``tests/migration_seed.py``).
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse
from djmoney.money import Money
from measurement.measures import Distance, Weight
from rest_framework import status
from rest_framework.test import APIClient

from product.factories import ProductFactory
from promotion.enum import BenefitType, PromotionTrigger, TargetScope
from promotion.factories import PromotionFactory
from shipping.enum import ShippingKind
from shipping.models import ShippingProvider, ShippingRate
from shipping.services import ShippingService
from shipping_acs import config as acs_config
from shipping_acs.services import _kg_from_grams
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db

BOXNOW_PRICE = Decimal("1.99")
ACS_PRICE = Decimal("2.99")
THRESHOLD = Decimal("39.90")

STORE_SETTINGS = {"PROMOTIONS_ENABLED": True}


@contextmanager
def _store_settings(**overrides):
    values = {**STORE_SETTINGS, **overrides}
    with patch(
        "extra_settings.models.Setting.get",
        side_effect=lambda key, default=None: values.get(key, default),
    ):
        yield


@pytest.fixture(autouse=True)
def _webside_like_store(acs_and_boxnow_configured_tenant, acs_locker_station):
    """GR by BOX NOW locker at 1,99 € and ACS home and pickup at
    2,99 €, all free from 39,90 €, the shape of the first store's
    rates; ``flat_rate`` off."""
    ShippingProvider.objects.filter(code__in=["acs", "boxnow"]).update(
        is_active=True
    )
    ShippingProvider.objects.exclude(code__in=["acs", "boxnow"]).update(
        is_active=False
    )
    for code, price in (("boxnow", BOXNOW_PRICE), ("acs", ACS_PRICE)):
        ShippingRate.objects.filter(
            provider__code=code, zone__country_id="GR"
        ).update(price=price, free_shipping_threshold=THRESHOLD, is_active=True)
    with _store_settings():
        yield


def _product(price: Decimal = Decimal("10.00"), **fields):
    return ProductFactory(
        stock=10,
        price=Money(price, "EUR"),
        discount_percent=Decimal(0),
        vat=VatFactory(value=0),
        active=True,
        num_images=0,
        num_reviews=0,
        **fields,
    )


def _too_big_for_a_locker(product):
    product.length = Distance(cm=80)
    product.width = Distance(cm=50)
    product.height = Distance(cm=40)
    product.save()
    return product


def _get(product) -> dict:
    response = APIClient().get(
        reverse("shipping-product", kwargs={"product_id": product.pk})
    )
    assert response.status_code == status.HTTP_200_OK
    return response.json()["estimate"]


def test_an_anonymous_visitor_gets_the_cheapest_delivery():
    estimate = _get(_product())

    assert estimate == {
        "countryCode": "GR",
        "price": 1.99,
        "currency": "EUR",
        "free": False,
        "freeShippingThreshold": 39.9,
    }


def test_a_product_priced_at_the_threshold_ships_free():
    estimate = _get(_product(THRESHOLD))

    assert estimate["price"] == 0
    assert estimate["free"] is True


def test_the_threshold_is_judged_after_an_automatic_discount():
    """A product listed at 40 € with 10% off every product costs a cart
    36 €: under the threshold, as checkout judges it."""
    PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.PERCENTAGE,
        benefit_value=Decimal(10),
    )

    estimate = _get(_product(Decimal("40.00")))

    assert estimate["price"] == 1.99
    assert estimate["free"] is False


def test_a_product_too_big_for_a_locker_is_priced_by_the_courier():
    product = _too_big_for_a_locker(_product())

    estimate = _get(product)

    assert estimate["price"] == 2.99


def test_a_product_over_a_rates_weight_cap_skips_that_rate():
    ShippingRate.objects.filter(
        provider__code="boxnow", zone__country_id="GR"
    ).update(max_weight_grams=1000)

    estimate = _get(_product(weight=Weight(g=1500)))

    assert estimate["price"] == 2.99


def test_the_threshold_is_the_lowest_of_the_carriers_that_take_it():
    ShippingRate.objects.filter(
        provider__code="boxnow", zone__country_id="GR"
    ).update(free_shipping_threshold=Decimal("19.90"))
    product = _too_big_for_a_locker(_product())

    estimate = _get(product)

    assert estimate["freeShippingThreshold"] == 39.9


def test_a_product_no_carrier_takes_has_no_estimate():
    ShippingProvider.objects.filter(code="acs").update(is_active=False)
    product = _too_big_for_a_locker(_product())

    assert _get(product) is None


def test_a_store_that_ships_nowhere_has_no_estimate():
    ShippingProvider.objects.update(is_active=False)

    assert _get(_product()) is None


def test_an_automatic_free_shipping_promotion_waives_it():
    PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.FREE_SHIPPING,
    )

    estimate = _get(_product())

    assert estimate["price"] == 0
    assert estimate["free"] is True


def test_a_free_shipping_promotion_for_other_products_does_not():
    promotion = PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.FREE_SHIPPING,
        target_scope=TargetScope.PRODUCTS,
    )
    promotion.products.add(_product())

    assert _get(_product())["free"] is False


def test_a_free_shipping_promotion_the_product_alone_misses_does_not():
    PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.FREE_SHIPPING,
        min_subtotal=Money(Decimal("25.00"), "EUR"),
    )

    assert _get(_product())["free"] is False
    assert _get(_product(Decimal("25.00")))["free"] is True


def test_a_free_shipping_code_is_not_applied():
    PromotionFactory(
        trigger=PromotionTrigger.CODE,
        benefit_type=BenefitType.FREE_SHIPPING,
    )

    assert _get(_product())["free"] is False


def test_promotions_switched_off_grant_nothing():
    PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.FREE_SHIPPING,
    )

    with _store_settings(PROMOTIONS_ENABLED=False):
        assert _get(_product())["free"] is False


def test_a_live_priced_carrier_is_priced_live():
    """ACS priced over ``ACS_Price_Calculation``, as checkout prices it,
    for the parcel's weight bracket."""
    ShippingProvider.objects.filter(code="boxnow").update(is_active=False)
    ShippingRate.objects.filter(
        provider__code="acs",
        zone__country_id="GR",
        kind=ShippingKind.PICKUP_POINT.value,
    ).update(is_active=False)
    product = _product(weight=Weight(g=1500))
    with (
        _store_settings(ACS_DYNAMIC_PRICING_ENABLED=True),
        patch.object(acs_config, "is_configured", return_value=True),
        patch.object(acs_config, "station_origin", return_value="ΑΚ"),
        patch("shipping_acs.client.AcsClient") as client,
    ):
        client.return_value.price_calculation.return_value = {
            "Total_Ammount": 5.21
        }
        cache.clear()
        estimate = _get(product)

    assert estimate["price"] == 5.21
    (payload,), _kwargs = client.return_value.price_calculation.call_args
    assert payload["Weight"] == _kg_from_grams(2000)


def test_an_inactive_product_is_not_found():
    product = _product()
    product.active = False
    product.save()

    response = APIClient().get(
        reverse("shipping-product", kwargs={"product_id": product.pk})
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_the_service_answers_what_the_endpoint_serves():
    product = _product()

    estimate = ShippingService.product_shipping_from(product)

    assert estimate is not None
    assert estimate.price == Money(BOXNOW_PRICE, "EUR")
    assert estimate.country_code == "GR"
    assert estimate.free_shipping_threshold == THRESHOLD
    assert estimate.free is False
