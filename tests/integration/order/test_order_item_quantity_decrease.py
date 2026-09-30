"""Lowering an existing ``OrderItem.quantity`` puts the difference back on
the shelf (``handle_order_item_post_save`` in
``order/signals/handlers.py`` → ``StockManager.adjust_stock`` in
``order/stock.py``).

The increase direction is covered in ``test_signals.py``. The movement
is logged against the order, which is what lets ``cancel_order`` later
restore only the stock the order still holds.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from order.models.history import OrderItemHistory
from order.models.stock_log import StockLog
from order.services import OrderService
from order.stock import StockManager
from product.factories.product import ProductFactory

pytestmark = pytest.mark.django_db


def _order_line(stock: int, quantity: int, **order_kwargs):
    product = ProductFactory(stock=stock, active=True)
    order = OrderFactory(num_order_items=0, **order_kwargs)
    item = order.items.create(
        product=product,
        price=Money(Decimal("10.00"), "EUR"),
        quantity=quantity,
    )
    return product, order, item


def test_decreasing_quantity_restocks_the_difference_against_the_order():
    product, order, item = _order_line(stock=10, quantity=5)

    item.quantity = 3
    item.save()

    product.refresh_from_db()
    assert product.stock == 12
    log = StockLog.objects.get(product=product)
    assert log.operation_type == StockLog.OPERATION_INCREMENT
    assert log.quantity_delta == 2
    assert log.stock_before == 10
    assert log.stock_after == 12
    assert log.order_id == order.id
    assert log.reason == "admin order item edit"


def test_decreasing_quantity_records_item_history():
    _product, _order, item = _order_line(stock=10, quantity=5)

    item.quantity = 1
    item.save()

    entry = OrderItemHistory.objects.get(
        order_item=item, change_type="QUANTITY"
    )
    assert entry.previous_value == {"quantity": 5}
    assert entry.new_value == {"quantity": 1}


def test_saving_without_a_quantity_change_moves_no_stock():
    product, _order, item = _order_line(stock=10, quantity=5)

    item.price = Money(Decimal("12.00"), "EUR")
    item.save()

    product.refresh_from_db()
    assert product.stock == 10
    assert not StockLog.objects.filter(product=product).exists()


def test_cancel_after_a_decrease_restores_only_what_the_order_still_holds():
    product, order, item = _order_line(
        stock=10, quantity=5, status=OrderStatus.PENDING
    )
    # Checkout takes the ordered quantity off the shelf.
    StockManager.decrement_stock(
        product_id=product.id, quantity=5, order_id=order.id
    )
    product.refresh_from_db()
    assert product.stock == 5

    item.quantity = 3
    item.save()
    product.refresh_from_db()
    assert product.stock == 7

    OrderService.cancel_order(order, refund_payment=False)

    product.refresh_from_db()
    assert product.stock == 10
    net = sum(
        StockLog.objects.filter(
            order_id=order.id,
            operation_type__in=(
                StockLog.OPERATION_DECREMENT,
                StockLog.OPERATION_INCREMENT,
            ),
        ).values_list("quantity_delta", flat=True)
    )
    assert net == 0
