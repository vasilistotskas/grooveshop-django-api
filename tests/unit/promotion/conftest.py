from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

from cart.factories import CartFactory, CartItemFactory
from product.factories import ProductFactory


@pytest.fixture
def enable_promotions():
    """Flip the PROMOTIONS_ENABLED extra-setting on for the test."""

    def _get(key, default=None):
        return {"PROMOTIONS_ENABLED": True}.get(key, default)

    with patch("promotion.services.Setting.get", side_effect=_get):
        yield


@pytest.fixture
def make_cart(db):
    """Build a cart with deterministic line prices.

    ``lines`` is a list of ``(price, quantity)`` tuples; products are
    created with no VAT and no markdown so ``final_price == price`` and
    the items total is exactly ``sum(price * qty)``.
    """

    def _make(lines, user=None):
        cart = CartFactory(user=user) if user else CartFactory(is_guest=True)
        cart.items.all().delete()
        products = []
        for price, quantity in lines:
            product = ProductFactory(
                price=Money(Decimal(str(price)), "EUR"),
                discount_percent=Decimal("0"),
                vat=None,
                stock=1000,
                active=True,
            )
            CartItemFactory(cart=cart, product=product, quantity=quantity)
            products.append(product)
        return cart, products

    return _make
