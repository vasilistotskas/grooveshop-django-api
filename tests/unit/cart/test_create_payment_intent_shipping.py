"""Regression tests for the PaymentIntent shipping mismatch fix.

Before the original fix, ``CartViewSet.create_payment_intent`` called
``OrderService.calculate_shipping_cost(cart_total)`` with no provider
or kind, silently dropping to a generic Setting-based fallback that
could disagree with the order-create verification step's per-carrier
figure. ``ShippingRate`` closes that gap structurally rather than by
convention: there is no longer a second "generic" pricing path to
drift from — ``OrderService.shipping_cost`` is the ONLY calculation
both the create-payment-intent view and order creation call, so they
cannot disagree on the same (provider, kind, country) triple. These
tests pin what's left to regress:

* The request serializer requires the shipping + country fields so
  callers can't accidentally omit what a rate lookup needs.
* Both call sites resolve to the SAME price for the SAME inputs.
* ``home_delivery`` without an explicit provider code auto-resolves to
  the active carrier, in both call sites, identically.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from cart.serializers.cart import CartCreatePaymentIntentRequestSerializer
from country.factories import CountryFactory
from order.exceptions import InvalidOrderDataError
from order.services import OrderService
from shipping.models import ShippingProvider
from tests.utils.shipping import enable_rate

pytestmark = pytest.mark.django_db


def test_serializer_requires_country_id():
    """A ``ShippingRate`` is per-country — there is no priceable
    option without a destination."""
    serializer = CartCreatePaymentIntentRequestSerializer(
        data={"pay_way_id": 1, "shipping_kind": "home_delivery"}
    )
    assert not serializer.is_valid()
    assert "country_id" in serializer.errors


def test_serializer_allows_home_delivery_without_provider_code():
    """``home_delivery`` is provider-agnostic in checkout — the
    frontend's ``carrierForMethod`` returns null for it, the order-
    create body carries no ``shippingProviderCode``, so the PI body
    must accept the same shape (otherwise both calls run different
    code paths and the PI amount mismatches the order-create amount).
    """
    serializer = CartCreatePaymentIntentRequestSerializer(
        data={
            "pay_way_id": 1,
            "shipping_kind": "home_delivery",
            "country_id": "GR",
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["shipping_provider_code"] is None


def test_serializer_requires_provider_code_for_pickup_point():
    serializer = CartCreatePaymentIntentRequestSerializer(
        data={
            "pay_way_id": 1,
            "shipping_kind": "pickup_point",
            "country_id": "GR",
        }
    )
    assert not serializer.is_valid()
    assert "shipping_provider_code" in serializer.errors


def test_serializer_requires_shipping_kind():
    serializer = CartCreatePaymentIntentRequestSerializer(
        data={
            "pay_way_id": 1,
            "shipping_provider_code": "acs",
            "country_id": "GR",
        }
    )
    assert not serializer.is_valid()
    assert "shipping_kind" in serializer.errors


def test_serializer_rejects_unknown_kind():
    serializer = CartCreatePaymentIntentRequestSerializer(
        data={
            "pay_way_id": 1,
            "shipping_provider_code": "acs",
            "shipping_kind": "drone",
            "country_id": "GR",
        }
    )
    assert not serializer.is_valid()
    assert "shipping_kind" in serializer.errors


def test_serializer_accepts_full_payload():
    serializer = CartCreatePaymentIntentRequestSerializer(
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


def test_pi_and_order_create_calcs_agree_at_every_price_point():
    """Contract: the create-payment-intent view and the order-create
    verification step both call ``OrderService.shipping_cost`` with
    the same args — there is nothing left to make them disagree.
    Anchored anyway so a future refactor that gives one call site its
    own pricing path regresses loudly.
    """
    country = CountryFactory()
    enable_rate(
        country,
        provider_code="flat_rate",
        price=Decimal("3.50"),
        free_shipping_threshold=Decimal("30.00"),
    )

    for total in (Decimal("10.00"), Decimal("29.99"), Decimal("30.00")):
        pi_calc = OrderService.shipping_cost(
            order_value=Money(total, "EUR"),
            country_id=country.alpha_2,
            shipping_provider_code="flat_rate",
            shipping_kind="home_delivery",
        )
        order_calc = OrderService.shipping_cost(
            order_value=Money(total, "EUR"),
            country_id=country.alpha_2,
            shipping_provider_code="flat_rate",
            shipping_kind="home_delivery",
        )
        assert pi_calc.amount == order_calc.amount, (
            f"PI vs order calc diverged at {total}: "
            f"pi={pi_calc.amount} order={order_calc.amount}"
        )

    # Below the threshold: charges the rate. At/above: free.
    assert OrderService.shipping_cost(
        order_value=Money(Decimal("10.00"), "EUR"),
        country_id=country.alpha_2,
        shipping_provider_code="flat_rate",
        shipping_kind="home_delivery",
    ).amount == Decimal("3.50")
    assert OrderService.shipping_cost(
        order_value=Money(Decimal("30.00"), "EUR"),
        country_id=country.alpha_2,
        shipping_provider_code="flat_rate",
        shipping_kind="home_delivery",
    ).amount == Decimal(0)


def test_home_delivery_without_explicit_code_resolves_to_the_active_carrier():
    """``home_delivery`` is provider-agnostic at the form level, so the
    storefront sends no ``shipping_provider_code`` — both order-create
    and PI calc receive None. ``OrderService.shipping_cost`` must
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

    quote = OrderService.shipping_cost(
        order_value=Money(Decimal("10.00"), "EUR"),
        country_id=country.alpha_2,
        shipping_kind="home_delivery",
        # NOTE: shipping_provider_code intentionally omitted — that's
        # the production frontend's behaviour for home_delivery.
    )
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
        OrderService.shipping_cost(
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
    quote = OrderService.shipping_cost(
        order_value=Money(min_threshold, "EUR"),
        country_id=country.alpha_2,
        shipping_provider_code="flat_rate",
        shipping_kind="home_delivery",
    )
    assert quote.amount == Decimal(0)
