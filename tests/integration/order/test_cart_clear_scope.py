"""Paying for an order clears that order's lines, not the whole cart.

An online order deliberately leaves the cart standing until the payment
lands — ``handle_order_created`` skips the clear while
``awaits_online_payment``, so a shopper who presses Back still has a
basket — and a hosted payment page can stay open for hours. Anything the
shopper adds during that window belongs to them, not to the order being
paid for.

The cart is a per-user singleton, so the order's cart and the shopper's
current cart are the same row. Only the LINES tell them apart.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from cart.models import Cart
from order.factories.order import OrderFactory
from order.models.item import OrderItem
from order.signals.handlers import _clear_cart_for_order
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _ordered(order, product, quantity):
    return OrderItem.objects.create(
        order=order,
        product=product,
        quantity=quantity,
        price=Money(Decimal("10.00"), "EUR"),
        sort_order=1,
    )


def test_an_item_added_while_the_payment_was_open_survives():
    user = UserAccountFactory(num_addresses=0)
    ordered_product = ProductFactory(num_images=0, num_reviews=0)
    added_later = ProductFactory(num_images=0, num_reviews=0)

    cart = CartFactory(user=user, num_cart_items=0)
    CartItemFactory(cart=cart, product=ordered_product, quantity=2)

    order = OrderFactory(user=user, num_order_items=0)
    _ordered(order, ordered_product, 2)

    # The shopper goes back to the store while the hosted page is open.
    CartItemFactory(cart=cart, product=added_later, quantity=1)

    _clear_cart_for_order(order)

    cart.refresh_from_db()
    remaining = list(cart.items.values_list("product_id", flat=True))
    assert remaining == [added_later.id], (
        "the shopper's newly added item was destroyed with the order's"
    )


def test_a_cart_holding_only_the_order_is_removed():
    user = UserAccountFactory(num_addresses=0)
    product = ProductFactory(num_images=0, num_reviews=0)

    cart = CartFactory(user=user, num_cart_items=0)
    CartItemFactory(cart=cart, product=product, quantity=3)

    order = OrderFactory(user=user, num_order_items=0)
    _ordered(order, product, 3)

    _clear_cart_for_order(order)

    assert not Cart.objects.filter(pk=cart.pk).exists()


def test_extra_quantity_of_an_ordered_product_is_kept():
    """Two of X were ordered; the shopper then added a third."""
    user = UserAccountFactory(num_addresses=0)
    product = ProductFactory(num_images=0, num_reviews=0)

    cart = CartFactory(user=user, num_cart_items=0)
    CartItemFactory(cart=cart, product=product, quantity=3)

    order = OrderFactory(user=user, num_order_items=0)
    _ordered(order, product, 2)

    _clear_cart_for_order(order)

    cart.refresh_from_db()
    assert cart.items.get(product=product).quantity == 1


def test_a_line_the_cart_never_had_removes_nothing():
    """Promotion gifts are injected onto the order at creation and were
    never cart items."""
    user = UserAccountFactory(num_addresses=0)
    ordered_product = ProductFactory(num_images=0, num_reviews=0)
    gift = ProductFactory(num_images=0, num_reviews=0)

    cart = CartFactory(user=user, num_cart_items=0)
    CartItemFactory(cart=cart, product=ordered_product, quantity=1)

    order = OrderFactory(user=user, num_order_items=0)
    _ordered(order, ordered_product, 1)
    _ordered(order, gift, 1)

    _clear_cart_for_order(order)

    assert not Cart.objects.filter(pk=cart.pk).exists()


def test_guest_cart_is_scoped_by_the_snapshot_uuid():
    product = ProductFactory(num_images=0, num_reviews=0)
    # Created directly: CartFactory does get_or_create on ``user``, so a
    # second CartFactory(user=None) hands back the same guest row.
    guest_cart = Cart.objects.create(user=None)
    CartItemFactory(cart=guest_cart, product=product, quantity=1)

    other_cart = Cart.objects.create(user=None)
    CartItemFactory(cart=other_cart, product=product, quantity=1)

    order = OrderFactory(user=None, num_order_items=0)
    order.metadata = {"cart_snapshot": {"cart_uuid": str(guest_cart.uuid)}}
    order.save(update_fields=["metadata"])
    _ordered(order, product, 1)

    _clear_cart_for_order(order)

    assert not Cart.objects.filter(pk=guest_cart.pk).exists()
    assert Cart.objects.filter(pk=other_cart.pk).exists()
