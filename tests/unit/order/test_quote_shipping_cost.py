"""``OrderQuoteService.shipping_cost``: the one delivery price.

The quote and order creation both price the delivery through it, so
there is no second pricing path to drift from. What is pinned here:

* a rate charges below its free-shipping threshold and nothing at it;
* ``home_delivery`` without an explicit provider code resolves to the
  active carrier, as order creation resolves its provider;
* no active carrier is a refusal, not a stale fallback price;
* the "free shipping above X" notice agrees with what the carrier
  charges at the boundary.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from country.factories import CountryFactory
from order.checkout.quote import OrderQuoteService
from order.exceptions import InvalidOrderDataError
from shipping.models import ShippingProvider
from tests.utils.shipping import enable_rate

pytestmark = pytest.mark.django_db


def test_a_rate_charges_below_its_threshold_and_nothing_at_it():
    country = CountryFactory()
    enable_rate(
        country,
        provider_code="flat_rate",
        price=Decimal("3.50"),
        free_shipping_threshold=Decimal("30.00"),
    )

    def price(total):
        return OrderQuoteService.shipping_cost(
            order_value=Money(Decimal(total), "EUR"),
            country_id=country.alpha_2,
            shipping_provider_code="flat_rate",
            shipping_kind="home_delivery",
        ).price.amount

    assert price("10.00") == Decimal("3.50")
    assert price("29.99") == Decimal("3.50")
    assert price("30.00") == Decimal(0)


def test_home_delivery_without_explicit_code_resolves_to_the_active_carrier():
    """``home_delivery`` is provider-agnostic at the form level, so the
    storefront sends no ``shipping_provider_code`` — both order creation
    and the quote receive None. ``OrderQuoteService.shipping_cost`` must
    auto-resolve to the active home-delivery provider for the
    destination country (mirroring ``_resolve_shipping_provider``'s FK
    resolution), so the cost matches the carrier the order will
    actually be served by.
    """
    country = CountryFactory()
    enable_rate(
        country,
        provider_code="flat_rate",
        price=Decimal("3.50"),
        free_shipping_threshold=Decimal("30.00"),
    )

    quote = OrderQuoteService.shipping_cost(
        order_value=Money(Decimal("10.00"), "EUR"),
        country_id=country.alpha_2,
        shipping_kind="home_delivery",
        # NOTE: shipping_provider_code intentionally omitted — that's
        # the production frontend's behaviour for home_delivery.
    ).price
    assert quote.amount == Decimal("3.50")


def test_no_active_provider_raises_invalid_order_data_error():
    """If ops deactivates every home-delivery provider for a country,
    there is genuinely nothing to quote — a 400, not a stale fallback
    price."""
    country = CountryFactory()
    ShippingProvider.objects.filter(supports_home_delivery=True).update(
        is_active=False
    )
    with pytest.raises(InvalidOrderDataError):
        OrderQuoteService.shipping_cost(
            order_value=Money(Decimal("35.00"), "EUR"),
            country_id=country.alpha_2,
            shipping_kind="home_delivery",
        )


def test_free_shipping_info_min_matches_carrier_at_threshold():
    """The "free shipping above X €" notice MUST agree with what the
    carrier actually charges at the boundary. If marketing copy says
    30€ but the carrier still charges at 30€, the cart's "qualified"
    UI state lies to the customer.
    """
    from shipping.services import ShippingService

    country = CountryFactory()
    enable_rate(
        country,
        provider_code="flat_rate",
        price=Decimal("2.50"),
        free_shipping_threshold=Decimal("30.00"),
    )

    info = ShippingService.free_shipping_info(country_code=country.alpha_2)
    min_threshold = info["min_threshold"]
    assert min_threshold == Decimal("30.00")

    # At the boundary, the carrier with the min threshold ships free.
    quote = OrderQuoteService.shipping_cost(
        order_value=Money(min_threshold, "EUR"),
        country_id=country.alpha_2,
        shipping_provider_code="flat_rate",
        shipping_kind="home_delivery",
    ).price
    assert quote.amount == Decimal(0)
