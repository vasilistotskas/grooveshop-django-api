"""CartItem money properties under a bound B2B pricing context.

The binding contract: every price property answers with the wholesale
figure when the cart instance carries ``_b2b_pricing``, and falls back
to retail otherwise — so all non-cart consumers of the same properties
stay retail without knowing B2B exists.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

from b2b.factories import BusinessProfileFactory, CustomerGroupFactory
from b2b.services import B2BPricingService
from cart.factories import CartFactory, CartItemFactory
from product.factories import ProductFactory
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db


def _cart_with_line(price="100.00", quantity=2, vat_rate="24"):
    cart = CartFactory(is_guest=True)
    cart.items.all().delete()
    product = ProductFactory(
        price=Money(Decimal(price), "EUR"),
        discount_percent=Decimal("0"),
        vat=VatFactory(value=Decimal(vat_rate)),
        stock=100,
        active=True,
    )
    item = CartItemFactory(cart=cart, product=product, quantity=quantity)
    return cart, item, product


def _bind(cart, group):
    from b2b.services import B2BPricingContext

    cart._b2b_pricing = B2BPricingContext(
        group=group,
        prices=B2BPricingService.resolve_map(
            [item.product for item in cart.items.all()], group
        ),
    )


class TestBoundProperties:
    def test_bound_prices(self):
        cart, item, _product = _cart_with_line()
        group = CustomerGroupFactory(discount_percent=Decimal("10"))
        _bind(cart, group)

        item = cart.items.select_related("product").first()

        assert item.price.amount == Decimal("90.00")  # net
        assert item.final_price.amount == Decimal("111.60")
        assert item.vat_value.amount == Decimal("21.60")
        assert item.total_price.amount == Decimal("223.20")
        assert item.discount_value.amount == Decimal("10.00")  # net saving
        assert item.total_discount_value.amount == Decimal("20.00")
        assert item.discount_percent == Decimal("10")

    def test_unbound_cart_stays_retail(self):
        cart, _item, product = _cart_with_line()

        item = cart.items.select_related("product").first()

        assert item.price.amount == Decimal("100.00")
        assert item.final_price.amount == product.final_price.amount
        assert item.total_price.amount == 2 * product.final_price.amount

    def test_cart_totals_read_bound_lines(self):
        cart, _item, _product = _cart_with_line()
        group = CustomerGroupFactory(discount_percent=Decimal("50"))
        _bind(cart, group)

        # Cart.total_price sums item.total_price — the payment-intent
        # amount and shipping thresholds read THIS, so it must be the
        # wholesale figure.
        assert cart.total_price.amount == Decimal("124.00")  # 2 × 62.00

    def test_price_at_add_snapshots_bound_price(self):
        cart, _item, _product = _cart_with_line()
        group = CustomerGroupFactory(discount_percent=Decimal("10"))
        _bind(cart, group)

        extra_product = ProductFactory(
            price=Money(Decimal("50.00"), "EUR"),
            discount_percent=Decimal("0"),
            vat=None,
            stock=100,
            active=True,
        )
        # Deliberately NOT pre-resolved into the map: the add-to-cart
        # request binds BEFORE the new item exists, so the snapshot
        # relies on the context's lazy per-product resolution.
        new_item = CartItemFactory(cart=cart, product=extra_product, quantity=1)

        assert new_item.price_at_add.amount == Decimal("45.00")


class TestBindCart:
    def test_bind_cart_resolves_group_from_user(
        self,
    ):
        group = CustomerGroupFactory(discount_percent=Decimal("10"))
        profile = BusinessProfileFactory(approved=True, customer_group=group)
        cart = CartFactory(user=profile.user)
        cart.items.all().delete()
        product = ProductFactory(
            price=Money(Decimal("100.00"), "EUR"),
            discount_percent=Decimal("0"),
            vat=None,
            stock=10,
            active=True,
        )
        CartItemFactory(cart=cart, product=product, quantity=1)

        def _get(key, default=None):
            return {"B2B_WHOLESALE_ENABLED": True}.get(key, default)

        with patch("b2b.services.Setting.get", side_effect=_get):
            context = B2BPricingService.bind_cart(cart, profile.user)

        assert context is not None
        assert B2BPricingService.cart_pricing_active(cart) is True
        assert cart.total_price.amount == Decimal("90.00")

    def test_bind_cart_noop_for_guest(self):
        cart, _item, product = _cart_with_line()

        context = B2BPricingService.bind_cart(cart, None)

        assert context is None
        assert B2BPricingService.cart_pricing_active(cart) is False
        assert cart.total_price.amount == 2 * product.final_price.amount
