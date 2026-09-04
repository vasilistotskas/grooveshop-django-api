"""Largest-remainder discount allocation invariants.

The invoice PDF and the AADE myDATA submission both reduce per-line
gross values by the order-level discount; the rounded lines MUST sum
exactly to (items total − discount) or AADE rejects the document
(errors 203 / 207-210).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from order.discounts import discounted_line_gross, order_discount_total

pytestmark = pytest.mark.django_db


def _order_with_lines(lines, discount=None, loyalty=None):
    from djmoney.money import Money as M

    from order.factories.order import OrderFactory
    from product.factories import ProductFactory

    order = OrderFactory(num_order_items=0)
    order.items.all().delete()
    for price, quantity in lines:
        product = ProductFactory(
            price=M(Decimal(str(price)), "EUR"),
            discount_percent=Decimal(0),
            vat=None,
            stock=100,
        )
        order.items.create(
            product=product,
            price=M(Decimal(str(price)), "EUR"),
            quantity=quantity,
        )
    if discount is not None:
        order.discount_amount = Money(Decimal(str(discount)), "EUR")
    if loyalty is not None:
        order.loyalty_discount = Money(Decimal(str(loyalty)), "EUR")
    order.save()
    return order


class TestAllocation:
    def test_no_discount_returns_full_gross(self):
        order = _order_with_lines([(10, 2), (5, 1)])

        allocation = discounted_line_gross(order)

        assert sorted(allocation.values()) == [
            Decimal(5),
            Decimal(20),
        ]

    def test_allocated_lines_sum_exactly_to_discounted_total(self):
        # 3 lines with awkward proportions force rounding remainders.
        order = _order_with_lines(
            [(19.99, 1), (7.77, 2), (3.33, 3)], discount="10.00"
        )
        items_total = Decimal("19.99") + Decimal("15.54") + Decimal("9.99")

        allocation = discounted_line_gross(order)

        assert sum(allocation.values()) == items_total - Decimal("10.00")
        assert all(
            value == value.quantize(Decimal("0.01"))
            for value in allocation.values()
        )

    def test_combines_promotion_and_loyalty_but_not_gift_card(self):
        order = _order_with_lines([(50, 1)], discount="5.00", loyalty="2.50")
        order.gift_card_amount = Money(Decimal("10.00"), "EUR")
        order.save(
            update_fields=["gift_card_amount", "gift_card_amount_currency"]
        )

        assert order_discount_total(order) == Decimal("7.50")
        allocation = discounted_line_gross(order)
        assert sum(allocation.values()) == Decimal("42.50")

    def test_discount_larger_than_items_clamps_to_zero(self):
        order = _order_with_lines([(10, 1), (5, 1)], discount="99.00")

        allocation = discounted_line_gross(order)

        assert sum(allocation.values()) == Decimal("0.00")
        assert all(value >= 0 for value in allocation.values())

    def test_no_line_goes_negative_near_full_discount(self):
        order = _order_with_lines([(0.03, 1), (99.97, 1)], discount="99.99")

        allocation = discounted_line_gross(order)

        assert sum(allocation.values()) == Decimal("0.01")
        assert all(value >= 0 for value in allocation.values())
