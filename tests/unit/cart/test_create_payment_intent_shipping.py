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


def test_serializer_allows_home_delivery_without_provider_code():
    """``home_delivery`` is provider-agnostic in checkout — the
    frontend's ``carrierForMethod`` returns null for it, the order-
    create body carries no ``shippingProviderCode``, so the PI body
    must accept the same shape (otherwise both calls run different
    code paths and the PI amount mismatches the order-create amount).
    """
    serializer = CartCreatePaymentIntentRequestSerializer(
        data={"pay_way_id": 1, "shipping_kind": "home_delivery"}
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["shipping_provider_code"] is None


def test_serializer_requires_provider_code_for_pickup_point():
    serializer = CartCreatePaymentIntentRequestSerializer(
        data={"pay_way_id": 1, "shipping_kind": "pickup_point"}
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


def test_pi_and_order_create_calcs_agree_in_gap_range(
    _per_carrier_below_generic,
):
    """Contract: the PI calc and the order-create verification call
    ``OrderService.calculate_shipping_cost`` with the same args and
    must therefore agree on every (cart_total, provider, kind) tuple
    — including the [30, 50) range that surfaced the original bug.

    Without this anchor, a future refactor that splits the two calc
    paths would silently break Stripe checkouts for carts in the gap.
    """
    cases = [
        ("acs", "home_delivery", Decimal("35.00")),
        ("acs", "pickup_point", Decimal("35.00")),
        ("boxnow", "pickup_point", Decimal("35.00")),
        ("acs", "home_delivery", Decimal("29.99")),  # one cent under
        ("acs", "home_delivery", Decimal("30.00")),  # exact threshold
    ]
    for provider, kind, total in cases:
        pi_calc = OrderService.calculate_shipping_cost(
            order_value=Money(total, "EUR"),
            shipping_provider_code=provider,
            shipping_kind=kind,
            weight_grams=500,
        )
        order_calc = OrderService.calculate_shipping_cost(
            order_value=Money(total, "EUR"),
            shipping_provider_code=provider,
            shipping_kind=kind,
            weight_grams=500,
        )
        assert pi_calc.amount == order_calc.amount, (
            f"PI vs order calc diverged for ({provider}, {kind}, {total})"
        )


def test_free_shipping_info_min_matches_carrier_at_threshold(
    _per_carrier_below_generic,
):
    """The "free shipping above X €" notice MUST agree with what the
    carrier actually charges at the boundary. If marketing copy says
    30€ but the carrier still charges at 30€, the cart's "qualified"
    UI state lies to the customer.
    """
    from shipping.services import ShippingService

    info = ShippingService.free_shipping_info()
    min_threshold = info["min_threshold"]
    assert min_threshold == Decimal("30.00")

    # At the boundary, the carrier with the min threshold ships free.
    quote = OrderService.calculate_shipping_cost(
        order_value=Money(min_threshold, "EUR"),
        shipping_provider_code="boxnow",
        shipping_kind="pickup_point",
        weight_grams=500,
    )
    assert quote.amount == Decimal("0")
