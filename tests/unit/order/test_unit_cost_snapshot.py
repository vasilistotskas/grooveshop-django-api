"""``OrderItem.unit_cost``: what a unit cost when it was sold, frozen.

The same rule as ``vat_rate``: copied from the product when the line is
created, so editing the product's cost later never restates a past sale.
A line with no cost is "no cost recorded", never a free one.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from order.factories.order import OrderFactory
from order.models import OrderItem
from product.factories import ProductFactory

pytestmark = pytest.mark.django_db

D = Decimal


def _line(product, **extra) -> OrderItem:
    order = OrderFactory(num_order_items=0)
    return OrderItem.objects.create(
        order=order,
        product=product,
        price=Money(D("12.40"), "EUR"),
        quantity=2,
        **extra,
    )


class TestTheSnapshot:
    def test_the_line_takes_the_products_cost_when_it_is_created(self):
        product = ProductFactory(cost_price=Money(D("4.25"), "EUR"))

        line = _line(product)

        line.refresh_from_db()
        assert line.unit_cost == Money(D("4.25"), "EUR")

    def test_editing_the_products_cost_later_leaves_the_sale_alone(self):
        product = ProductFactory(cost_price=Money(D("4.25"), "EUR"))
        line = _line(product)

        product.cost_price = Money(D("9.99"), "EUR")
        product.save()
        line.save()

        line.refresh_from_db()
        assert line.unit_cost == Money(D("4.25"), "EUR")

    def test_a_product_with_no_cost_gives_a_line_with_no_cost(self):
        product = ProductFactory(cost_price=None)

        line = _line(product)

        line.refresh_from_db()
        assert line.unit_cost is None

    def test_a_cost_added_to_the_product_later_does_not_reach_old_lines(self):
        product = ProductFactory(cost_price=None)
        line = _line(product)

        product.cost_price = Money(D("3.00"), "EUR")
        product.save()

        line.refresh_from_db()
        assert line.unit_cost is None

    def test_a_cost_the_caller_states_is_kept(self):
        product = ProductFactory(cost_price=Money(D("4.25"), "EUR"))

        line = _line(product, unit_cost=Money(D("1.00"), "EUR"))

        line.refresh_from_db()
        assert line.unit_cost == Money(D("1.00"), "EUR")

    def test_a_zero_cost_is_a_recorded_cost(self):
        """A free-of-charge input is a known cost of zero, not an unknown."""
        product = ProductFactory(cost_price=Money(D("0"), "EUR"))

        line = _line(product)

        line.refresh_from_db()
        assert line.unit_cost == Money(D("0"), "EUR")


class TestARestockNamesItsLine:
    def test_the_stock_log_of_a_returned_unit_points_at_the_line(self):
        from order.enum.status import OrderStatus
        from order.models import Order, StockLog

        product = ProductFactory(cost_price=Money(D("4.25"), "EUR"), stock=10)
        line = _line(product)
        Order.objects.filter(pk=line.order_id).update(
            status=OrderStatus.DELIVERED
        )

        line.refund(quantity=1)

        [log] = StockLog.objects.filter(
            operation_type=StockLog.OPERATION_INCREMENT
        )
        assert log.order_item_id == line.pk
        assert log.quantity_delta == 1
