"""VAT is owed on what the customer paid, not on the list price.

A price discount granted at the time of sale (coupon, automatic
promotion, redeemed loyalty points) reduces the taxable base; a gift card
is a means of payment and does not. The cart preview and the invoice
share ``order.discounts`` so the VAT line the shopper sees is the VAT the
invoice carries.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from cart.factories import CartFactory, CartItemFactory
from cart.serializers.cart import CartSerializer
from order.discounts import allocate_discount, vat_buckets
from order.factories.order import OrderFactory
from order.invoicing import _compute_vat_breakdown
from product.factories import ProductFactory
from promotion.enum import BenefitType, PromotionTrigger
from promotion.factories import PromotionFactory
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db

D = Decimal


@pytest.fixture
def enable_promotions():
    from unittest.mock import patch

    def _get(key, default=None):
        return {"PROMOTIONS_ENABLED": True}.get(key, default)

    with patch("promotion.services.Setting.get", side_effect=_get):
        yield


def _product(net, rate):
    return ProductFactory(
        price=Money(D(str(net)), "EUR"),
        discount_percent=D(0),
        vat=VatFactory(value=D(str(rate))),
        stock=1000,
    )


def _cart(lines):
    """``lines``: (net price, VAT rate, quantity) — line gross = net * (1+r)."""
    cart = CartFactory(is_guest=True)
    cart.items.all().delete()
    for net, rate, quantity in lines:
        CartItemFactory(
            cart=cart, product=_product(net, rate), quantity=quantity
        )
    return cart


def _coupon(amount, benefit=BenefitType.FIXED_AMOUNT):
    PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=benefit,
        benefit_value=D(str(amount)),
        stackable=True,
    )


def _cart_vat(cart):
    return CartSerializer(cart).data["total_vat_value"]


def _order(lines, discount=None, loyalty=None, gift_card=None):
    order = OrderFactory(num_order_items=0)
    order.items.all().delete()
    for net, rate, quantity in lines:
        product = _product(net, rate)
        order.items.create(
            product=product, price=product.final_price, quantity=quantity
        )
    if discount is not None:
        order.discount_amount = Money(D(str(discount)), "EUR")
    if loyalty is not None:
        order.loyalty_discount = Money(D(str(loyalty)), "EUR")
    if gift_card is not None:
        order.gift_card_amount = Money(D(str(gift_card)), "EUR")
    order.save()
    return order


def _invoice_vat(order):
    return sum(D(row["vat"]) for row in _compute_vat_breakdown(order))


class TestSharedFunctions:
    def test_allocation_sums_exactly_across_mixed_lines(self):
        gross = {1: D("124.00"), 2: D("113.00"), 3: D("10.01")}

        discounted = allocate_discount(gross, D("33.33"))

        assert sum(discounted.values()) == sum(gross.values()) - D("33.33")

    def test_buckets_reconcile_to_the_cent(self):
        buckets = vat_buckets([(D("111.60"), D("24")), (D("101.70"), D("13"))])

        for row in buckets.values():
            assert row["subtotal"] + row["vat"] == row["gross"]
        assert buckets[D("24")]["vat"] == D("21.60")
        assert buckets[D("13")]["vat"] == D("11.70")


class TestCartVat:
    def test_no_discount_is_the_line_vat(self, enable_promotions):
        cart = _cart([(100, 24, 2)])

        assert _cart_vat(cart) == D("48.00")

    def test_single_rate_coupon_reduces_the_base(self, enable_promotions):
        cart = _cart([(100, 24, 1)])
        _coupon(12.40)

        # gross 124.00 - 12.40 = 111.60 -> net 90.00, VAT 21.60
        assert _cart_vat(cart) == D("21.60")

    def test_mixed_rates_share_the_discount_proportionally(
        self, enable_promotions
    ):
        cart = _cart([(100, 24, 1), (100, 13, 1)])
        _coupon(23.70)

        # 237.00 gross, 10% off: 111.60 @24% + 101.70 @13%
        assert _cart_vat(cart) == D("33.30")

    def test_percentage_promotion_matches_the_invoice(self, enable_promotions):
        cart = _cart([(100, 24, 1), (50, 13, 3)])
        _coupon(15, BenefitType.PERCENTAGE)
        data = CartSerializer(cart).data
        order = _order(
            [(100, 24, 1), (50, 13, 3)], discount=data["promotion_discount"]
        )

        assert data["promotion_discount"] > 0
        assert data["total_vat_value"] == _invoice_vat(order)

    def test_free_shipping_does_not_touch_vat(self, enable_promotions):
        cart = _cart([(100, 24, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FREE_SHIPPING,
            stackable=True,
        )

        assert _cart_vat(cart) == D("24.00")

    def test_a_discount_larger_than_the_items_leaves_no_vat(
        self, enable_promotions
    ):
        cart = _cart([(10, 24, 1)])
        _coupon(500)

        assert _cart_vat(cart) == D("0.00")


class TestMarkdownVat:
    def test_cart_vat_matches_the_invoice_on_marked_down_products(self):
        product = ProductFactory(
            price=Money(D("100.00"), "EUR"),
            discount_percent=D(20),
            vat=VatFactory(value=D(24)),
            stock=1000,
        )
        cart = CartFactory(is_guest=True)
        cart.items.all().delete()
        CartItemFactory(cart=cart, product=product, quantity=2)
        order = OrderFactory(num_order_items=0)
        order.items.all().delete()
        order.items.create(
            product=product, price=product.final_price, quantity=2
        )

        # paid 2 x 104.00 = 208.00 -> VAT 40.26, not 2 x 24.00 on list net
        assert _cart_vat(cart) == _invoice_vat(order) == D("40.26")


class TestInvoiceVat:
    def test_coupon_plus_points_reduce_the_base(self):
        order = _order([(100, 24, 1)], discount="10.00", loyalty="2.40")

        # 124.00 - 12.40 = 111.60 -> VAT 21.60
        assert _invoice_vat(order) == D("21.60")

    def test_gift_card_does_not_change_vat(self):
        plain = _order([(100, 24, 1)])
        paid_with_card = _order([(100, 24, 1)], gift_card="50.00")

        assert _invoice_vat(paid_with_card) == _invoice_vat(plain) == D("24.00")
