"""`hasDiscounts` must judge each cart item on its own merits.

Django documents that "the conditions in a single `exclude()` call will
not necessarily refer to the same item" when spanning a multi-valued
relationship — only `filter()` guarantees that. The false branch used
`exclude(items__product__discount_percent__gt=0,
items__product__price__gt=0)`, so a cart whose two conditions were
satisfied by DIFFERENT items was excluded even though no single item is
actually discounted.

`Product.price` defaults to zero and the pairing is not a database
constraint, so the mixed cart is reachable.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from cart.factories.cart import CartFactory
from cart.filters.cart import CartFilter
from cart.models.cart import Cart
from cart.models.item import CartItem
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _cart_with(items):
    cart = CartFactory(user=UserAccountFactory())
    CartItem.objects.filter(cart=cart).delete()
    for discount, price in items:
        product = ProductFactory(
            num_images=0,
            num_reviews=0,
            discount_percent=Decimal(discount),
            price=Money(price, "EUR"),
        )
        CartItem.objects.create(cart=cart, product=product, quantity=1)
    return cart


def _matches(value):
    return set(
        CartFilter(
            {"hasDiscounts": value}, queryset=Cart.objects.all()
        ).qs.values_list("id", flat=True)
    )


def test_conditions_met_by_different_items_are_not_a_discount():
    # One item is 10% off but priced 0; another is priced 20 at 0% off.
    # Neither is discounted; together they satisfy both predicates.
    cart = _cart_with([("10", 0), ("0", 20)])

    assert not CartItem.objects.filter(
        cart=cart,
        product__discount_percent__gt=0,
        product__price__gt=0,
    ).exists()
    assert cart.id in _matches("false")
    assert cart.id not in _matches("true")


def test_a_genuinely_discounted_item_still_matches():
    cart = _cart_with([("10", 20)])

    assert cart.id in _matches("true")
    assert cart.id not in _matches("false")


def test_a_cart_with_no_discount_at_all_still_matches_false():
    cart = _cart_with([("0", 20)])

    assert cart.id in _matches("false")
    assert cart.id not in _matches("true")
