"""Regression tests for the PaymentIntent shipping mismatch fix.

Before the fix, ``CartViewSet.create_payment_intent`` called
``OrderService.calculate_shipping_cost(cart_total)`` with no provider
or kind, silently dropping to the generic ``FREE_SHIPPING_THRESHOLD``
fallback. The order-create verification step, however, passes the
chosen carrier/kind — so the two calls disagreed whenever the
per-carrier threshold differed from the generic one, raising
``PaymentAmountMismatchError`` at order-create time and breaking the
checkout for a real range of cart subtotals.

These tests pin the contract:

* The request serializer requires the shipping fields so callers can't
  accidentally re-introduce the silent fallback.
* The shipping calc with provider+kind returns 0€ at the per-carrier
  threshold even when the generic threshold would still charge — which
  is exactly the case for the production thresholds (ACS/BoxNow=30€,
  generic=50€).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money
from extra_settings.models import Setting

from cart.serializers.cart import CartCreatePaymentIntentRequestSerializer
from order.services import OrderService
from shipping.models import ShippingProvider

pytestmark = pytest.mark.django_db


@pytest.fixture
def _per_carrier_below_generic():
    """Match the production-tuned thresholds.

    Per-carrier 30€ vs generic 50€ is the configuration that surfaced
    the original bug — any cart in [30, 50) sees free shipping via the
    carrier but charged shipping via the generic fallback.
    """
    Setting.objects.update_or_create(
        name="ACS_FREE_SHIPPING_THRESHOLD",
        defaults={"value_type": "decimal", "value_decimal": Decimal("30.00")},
    )
    Setting.objects.update_or_create(
        name="BOXNOW_FREE_SHIPPING_THRESHOLD",
        defaults={"value_type": "decimal", "value_decimal": Decimal("30.00")},
    )
    Setting.objects.update_or_create(
        name="FREE_SHIPPING_THRESHOLD",
        defaults={"value_type": "decimal", "value_decimal": Decimal("50.00")},
    )
    ShippingProvider.objects.filter(code__in=["acs", "boxnow"]).update(
        is_active=True
    )


def test_serializer_requires_shipping_provider_code():
    serializer = CartCreatePaymentIntentRequestSerializer(
        data={"pay_way_id": 1, "shipping_kind": "home_delivery"}
    )
    assert not serializer.is_valid()
    assert "shipping_provider_code" in serializer.errors


def test_serializer_requires_shipping_kind():
    serializer = CartCreatePaymentIntentRequestSerializer(
        data={"pay_way_id": 1, "shipping_provider_code": "acs"}
    )
    assert not serializer.is_valid()
    assert "shipping_kind" in serializer.errors


def test_serializer_rejects_unknown_kind():
    serializer = CartCreatePaymentIntentRequestSerializer(
        data={
            "pay_way_id": 1,
            "shipping_provider_code": "acs",
            "shipping_kind": "drone",
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


def test_acs_pi_calc_returns_zero_in_mismatch_range(_per_carrier_below_generic):
    """Cart 35€ + ACS home delivery — fixed PI calc must return 0€.

    Mirrors what the view does now: passes provider+kind through to
    ``OrderService.calculate_shipping_cost``. Without the fix this
    call would return 2.99€ (generic fallback), disagreeing with the
    order-create verification's per-carrier 0€.
    """
    quote = OrderService.calculate_shipping_cost(
        order_value=Money(Decimal("35.00"), "EUR"),
        shipping_provider_code="acs",
        shipping_kind="home_delivery",
        weight_grams=500,
    )
    assert quote.amount == Decimal("0")


def test_boxnow_pi_calc_returns_zero_in_mismatch_range(
    _per_carrier_below_generic,
):
    """Cart 35€ + BoxNow pickup — fixed PI calc must return 0€."""
    quote = OrderService.calculate_shipping_cost(
        order_value=Money(Decimal("35.00"), "EUR"),
        shipping_provider_code="boxnow",
        shipping_kind="pickup_point",
        weight_grams=500,
    )
    assert quote.amount == Decimal("0")


def test_legacy_generic_fallback_unchanged(_per_carrier_below_generic):
    """No carrier code → generic fallback path with 50€ threshold.

    Anchored as a regression test so a future refactor doesn't change
    the generic path's behaviour for non-carrier-aware callers.
    """
    quote = OrderService.calculate_shipping_cost(
        order_value=Money(Decimal("35.00"), "EUR"),
    )
    # 35 < generic 50 → falls back to CHECKOUT_SHIPPING_PRICE
    assert quote.amount > Decimal("0")
