from decimal import Decimal

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from djmoney.money import Money

from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.models.item import OrderItem
from order.models.stock_log import StockLog
from product.factories.product import ProductFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), settings.DEFAULT_CURRENCY)


@pytest.fixture
def order():
    return OrderFactory.create(num_order_items=0)


@pytest.fixture
def product():
    product = ProductFactory.create(
        stock=20, price=_eur("50.00"), num_images=0, num_reviews=0
    )
    product.set_current_language("en")
    product.name = "Test Product"
    product.save()
    return product


@pytest.fixture
def order_item(order, product):
    return OrderItemFactory.create(
        order=order,
        product=product,
        price=_eur("20.00"),
        quantity=5,
        refunded_quantity=0,
        is_refunded=False,
    )


class TestOrderItemModel:
    def test_str_representation(self, order_item, order):
        assert str(order_item) == f"Order {order.id} - Test Product x 5"

    def test_save_records_the_original_quantity(self, order, product):
        item = OrderItem(
            order=order, product=product, price=product.price, quantity=2
        )
        item.save()

        item.refresh_from_db()
        assert item.original_quantity == 2

    def test_raising_quantity_takes_the_difference_from_stock(
        self, order_item, product
    ):
        product.refresh_from_db()
        initial_stock = product.stock

        order_item.quantity = 7
        order_item.save()

        product.refresh_from_db()
        assert product.stock == initial_stock - 2

    def test_money_properties(self, order_item):
        order_item.refunded_quantity = 2

        assert order_item.total_price == _eur("100.00")
        assert order_item.net_quantity == 3
        assert order_item.net_price == _eur("60.00")
        assert order_item.refunded_amount == _eur("40.00")

    def test_refunded_amount_is_zero_without_a_refund(self, order_item):
        assert order_item.refunded_amount == _eur("0.00")


class TestOrderItemClean:
    @pytest.mark.parametrize(
        "quantity,error",
        [
            (19, None),
            (20, None),
            (21, "The quantity exceeds the available stock."),
            (0, "Quantity must be greater than 0."),
        ],
    )
    def test_new_item_quantity_is_checked_against_stock(
        self, order, product, quantity, error
    ):
        item = OrderItem(
            order=order, product=product, price=product.price, quantity=quantity
        )

        if error is None:
            item.clean()
        else:
            with pytest.raises(ValidationError, match=error):
                item.clean()

    def test_existing_item_is_not_checked_against_stock(
        self, order_item, product
    ):
        product.stock = 0
        product.save()

        order_item.clean()

    def test_refunded_quantity_cannot_exceed_quantity(self, order_item):
        order_item.refunded_quantity = 6

        with pytest.raises(
            ValidationError, match="Refunded quantity cannot exceed"
        ):
            order_item.clean()


class TestOrderItemConstraints:
    def test_db_rejects_over_refund(self, order):
        """The CheckConstraint refuses refunded_quantity > quantity
        (G0247), whatever path writes the row."""
        with pytest.raises(IntegrityError), transaction.atomic():
            order.items.create(
                product=ProductFactory(num_images=0, num_reviews=0),
                price=_eur("20.00"),
                quantity=2,
                refunded_quantity=3,
            )

    def test_db_rejects_zero_quantity(self, order):
        with pytest.raises(IntegrityError), transaction.atomic():
            order.items.create(
                product=ProductFactory(num_images=0, num_reviews=0),
                price=_eur("20.00"),
                quantity=0,
            )


class TestOrderItemRefund:
    @pytest.mark.parametrize("quantity", [-1, 0, 6])
    def test_invalid_quantity_changes_nothing(self, order_item, quantity):
        with pytest.raises(ValidationError):
            OrderItem.refund(order_item, quantity)

        order_item.refresh_from_db()
        assert order_item.refunded_quantity == 0

    def test_partial_refund_restocks_and_returns_the_amount(
        self, order_item, product
    ):
        product.refresh_from_db()
        stock_before = product.stock

        amount = OrderItem.refund(order_item, 2)

        assert amount == _eur("40.00")
        order_item.refresh_from_db()
        assert order_item.refunded_quantity == 2
        assert order_item.is_refunded is False
        product.refresh_from_db()
        assert product.stock == stock_before + 2
        assert StockLog.objects.filter(
            product=product,
            order_id=order_item.order_id,
            operation_type=StockLog.OPERATION_INCREMENT,
            quantity_delta=2,
        ).exists()

    def test_refund_without_quantity_refunds_the_remainder(self, order_item):
        OrderItem.refund(order_item, 2)

        amount = OrderItem.refund(order_item)

        assert amount == _eur("60.00")
        order_item.refresh_from_db()
        assert order_item.refunded_quantity == 5
        assert order_item.is_refunded is True


class TestOrderItemQuerySet:
    @pytest.fixture
    def items(self, product):
        other_product = ProductFactory.create(
            stock=30, price=_eur("40.00"), num_images=0, num_reviews=0
        )
        first, second = OrderFactory.create_batch(2, num_order_items=0)
        return {
            "first_a": OrderItemFactory.create(
                order=first, product=product, price=_eur("25.00"), quantity=2
            ),
            "first_b": OrderItemFactory.create(
                order=first,
                product=other_product,
                price=_eur("40.00"),
                quantity=3,
            ),
            "second_a": OrderItemFactory.create(
                order=second, product=product, price=_eur("25.00"), quantity=1
            ),
        }

    def test_for_order(self, items):
        order_id = items["first_a"].order_id

        result = OrderItem.objects.for_order(order_id)

        assert set(result.values_list("id", flat=True)) == {
            items["first_a"].id,
            items["first_b"].id,
        }

    def test_for_product(self, items, product):
        result = OrderItem.objects.for_product(product.id)

        assert set(result.values_list("id", flat=True)) == {
            items["first_a"].id,
            items["second_a"].id,
        }

    def test_sum_quantities(self, items):
        order_id = items["first_a"].order_id

        assert OrderItem.objects.for_order(order_id).sum_quantities() == 5

    def test_total_items_cost(self, items):
        order_id = items["first_a"].order_id

        total = OrderItem.objects.for_order(order_id).total_items_cost()

        # 2 x 25 + 3 x 40
        assert total == _eur("170.00")

    def test_total_items_cost_of_an_empty_order_is_zero(self, order):
        total = OrderItem.objects.for_order(order.id).total_items_cost()

        assert total == _eur("0.00")
