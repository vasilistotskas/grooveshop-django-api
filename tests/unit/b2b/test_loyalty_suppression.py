"""Wholesale carts sit outside the loyalty program unless the merchant
opts in.

The accrual half already refuses to award points (see
``tests/unit/loyalty/test_b2b_accrual_gate.py``). These cover the
redemption half: leaving it open would let retail-basis points be spent
as a further discount on already-negotiated wholesale prices, and would
make the checkout advertise points that are never granted.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

from b2b.services import B2BPricingService, B2BService
from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from product.factories import ProductFactory

pytestmark = pytest.mark.django_db


def _settings(values):
    def _get(key, default=None):
        return values.get(key, default)

    return patch("b2b.services.Setting.get", side_effect=_get)


def _cart_for(user):
    cart = CartFactory(user=user, num_cart_items=0)
    product = ProductFactory(
        price=Money(Decimal("100.00"), "EUR"),
        discount_percent=Decimal(0),
        stock=50,
        active=True,
    )
    CartItemFactory(cart=cart, product=product, quantity=2)
    return cart


class TestSuppressesLoyalty:
    def test_retail_cart_is_untouched(self, approved_buyer):
        user, _group = approved_buyer()
        cart = _cart_for(user)

        # No bind at all => a retail cart, loyalty stays available.
        with _settings({"B2B_WHOLESALE_ENABLED": True}):
            assert B2BService.suppresses_loyalty(cart) is False

    def test_wholesale_cart_suppresses_by_default(self, approved_buyer):
        user, _group = approved_buyer()
        cart = _cart_for(user)

        with _settings({"B2B_WHOLESALE_ENABLED": True}):
            B2BPricingService.bind_cart(cart, user)
            assert B2BPricingService.cart_pricing_active(cart) is True
            assert B2BService.suppresses_loyalty(cart) is True

    def test_merchant_opt_in_restores_loyalty(self, approved_buyer):
        user, _group = approved_buyer()
        cart = _cart_for(user)

        with _settings(
            {"B2B_WHOLESALE_ENABLED": True, "B2B_LOYALTY_ENABLED": True}
        ):
            B2BPricingService.bind_cart(cart, user)
            assert B2BService.suppresses_loyalty(cart) is False

    def test_both_halves_read_one_switch(self):
        """Earning and redeeming must never disagree."""
        with _settings({"B2B_LOYALTY_ENABLED": True}):
            assert B2BService.loyalty_allowed() is True
        with _settings({}):
            assert B2BService.loyalty_allowed() is False
