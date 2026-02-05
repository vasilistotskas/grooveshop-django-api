import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from djmoney.money import Money

from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.models.item import OrderItem
from product.factories.product import ProductFactory


@pytest.fixture
def test_order():
    """Create a test order."""
    return OrderFactory.create()


@pytest.fixture
def test_product():
    """Create a test product with stock and price."""
    product = ProductFactory.create(
        stock=20, price=Money("50.00", settings.DEFAULT_CURRENCY)
    )
    product.set_current_language("en")
    product.name = "Test Product"
    product.save()
    return product


@pytest.fixture
def test_order_item(test_order, test_product):
    """Create a test order item."""
    return OrderItemFactory.create(
        order=test_order,
        product=test_product,
        quantity=3,
        refunded_quantity=0,
    )


@pytest.mark.django_db
class TestOrderItemModel:
    def test_str_representation(
        self, test_order_item, test_order, test_product
    ):
        expected = f"Order {test_order.id} - {test_product.name} x {test_order_item.quantity}"
        assert str(test_order_item) == expected

    def test_get_ordering_queryset(self, test_order_item):
        queryset = test_order_item.get_ordering_queryset()
        assert queryset is not None

    def test_clean_valid_quantity(self, test_order_item):
        test_order_item.clean()

    def test_clean_invalid_quantity(self, test_order_item):
        test_order_item.quantity = 0
        with pytest.raises(ValidationError):
            test_order_item.clean()

    def test_clean_refunded_quantity_too_high(self, test_order_item):
        test_order_item.refunded_quantity = 5
        with pytest.raises(ValidationError):
            test_order_item.clean()

    def test_clean_insufficient_stock(self, test_order, test_product):
        test_product.stock = 2
        test_product.save()

        new_item = OrderItem(
            order=test_order,
            product=test_product,
            price=test_product.price,
            quantity=5,
        )

        with pytest.raises(ValidationError):
            new_item.clean()

    def test_clean_existing_item_stock_not_checked(
        self, test_order_item, test_product
    ):
        test_order_item.save()
        test_product.stock = 0
        test_product.save()
        test_order_item.clean()

    def test_save_new_item(self, test_order, test_product):
        new_item = OrderItem(
            order=test_order,
            product=test_product,
            price=test_product.price,
            quantity=2,
        )
        new_item.save()

        new_item.refresh_from_db()

        assert new_item.original_quantity == 2

    def test_save_existing_item(self, test_order_item, test_product):
        initial_stock = test_product.stock
        test_order_item.quantity = 5
        test_order_item.save()

        test_product.refresh_from_db()
        assert test_product.stock == initial_stock - 2

    def test_total_price_property(self, test_order_item):
        expected_total = test_order_item.price * test_order_item.quantity
        assert test_order_item.total_price == expected_total

    def test_net_quantity_property(self, test_order_item):
        test_order_item.refunded_quantity = 1
        expected_net = (
            test_order_item.quantity - test_order_item.refunded_quantity
        )
        assert test_order_item.net_quantity == expected_net

    def test_net_price_property(self, test_order_item):
        test_order_item.refunded_quantity = 1
        expected_net_price = (
            test_order_item.price * test_order_item.net_quantity
        )
        assert test_order_item.net_price == expected_net_price

    def test_refunded_amount_property_no_refund(self, test_order_item):
        expected = Money("0.00", settings.DEFAULT_CURRENCY)
        assert test_order_item.refunded_amount == expected

    def test_refunded_amount_property_partial_refund(self, test_order_item):
        test_order_item.refunded_quantity = 1
        test_order_item.save()
        expected = test_order_item.price * test_order_item.refunded_quantity
        assert test_order_item.refunded_amount == expected

    def test_refund_invalid_quantity(self, test_order_item):
        test_order_item.quantity = 5
        test_order_item.refunded_quantity = 0

        with pytest.raises(ValidationError):
            OrderItem.refund(test_order_item, -1)

        assert test_order_item.refunded_quantity == 0

    def test_refund_too_much(self, test_order_item):
        test_order_item.quantity = 5
        test_order_item.refunded_quantity = 0

        with pytest.raises(ValidationError):
            OrderItem.refund(test_order_item, 6)

        assert test_order_item.refunded_quantity == 0

    def test_refund_partial(self, test_order_item):
        test_order_item.quantity = 5
        test_order_item.refunded_quantity = 0

        result = OrderItem.refund(test_order_item, 2)

        assert isinstance(result, Money)
        assert test_order_item.refunded_quantity == 2

    def test_refund_full(self, test_order_item):
        test_order_item.quantity = 5
        test_order_item.refunded_quantity = 0

        result = OrderItem.refund(test_order_item, 5)

        assert isinstance(result, Money)
        assert test_order_item.refunded_quantity == 5


@pytest.fixture
def test_order1():
    """Create first test order."""
    return OrderFactory.create(num_order_items=0)


@pytest.fixture
def test_order2():
    """Create second test order."""
    return OrderFactory.create(num_order_items=0)


@pytest.fixture
def test_product1():
    """Create first test product."""
    product = ProductFactory.create(
        stock=50, price=Money("25.00", settings.DEFAULT_CURRENCY)
    )
    product.set_current_language("en")
    product.name = "Product 1"
    product.save()
    return product


@pytest.fixture
def test_product2():
    """Create second test product."""
    product = ProductFactory.create(
        stock=30, price=Money("40.00", settings.DEFAULT_CURRENCY)
    )
    product.set_current_language("en")
    product.name = "Product 2"
    product.save()
    return product


@pytest.fixture
def test_items(test_order1, test_order2, test_product1, test_product2):
    """Create test order items."""
    item1 = OrderItemFactory.create(
        order=test_order1, product=test_product1, quantity=2
    )
    item2 = OrderItemFactory.create(
        order=test_order1, product=test_product2, quantity=3
    )
    item3 = OrderItemFactory.create(
        order=test_order2, product=test_product1, quantity=1
    )
    return {"item1": item1, "item2": item2, "item3": item3}


@pytest.mark.django_db
class TestOrderItemQuerySet:
    def test_for_order(self, test_order1, test_items):
        result = OrderItem.objects.for_order(test_order1.id)
        items = list(result)

        item_ids = [item.id for item in items]
        assert test_items["item1"].id in item_ids
        assert test_items["item2"].id in item_ids

        for item in items:
            assert item.order_id == test_order1.id

    def test_for_product(self, test_product1, test_items):
        result = OrderItem.objects.for_product(test_product1.id)
        items = list(result)

        item_ids = [item.id for item in items]
        assert test_items["item1"].id in item_ids
        assert test_items["item3"].id in item_ids

        for item in items:
            assert item.product_id == test_product1.id

    def test_with_product_data(self, test_items):
        result = OrderItem.objects.with_product_data()

        for item in result:
            assert item.product.name is not None

    def test_sum_quantities(self, test_order1, test_order2, test_items):
        result = OrderItem.objects.for_order(test_order1.id).sum_quantities()

        items_for_order1 = OrderItem.objects.filter(order=test_order1.id)
        expected_quantity = sum(item.quantity for item in items_for_order1)
        assert result == expected_quantity

        result2 = OrderItem.objects.for_order(test_order2.id).sum_quantities()
        items_for_order2 = OrderItem.objects.filter(order=test_order2.id)
        expected_quantity2 = sum(item.quantity for item in items_for_order2)
        assert result2 == expected_quantity2

        assert expected_quantity > 0
        assert expected_quantity2 > 0

    def test_total_items_cost(self, test_order1):
        result = OrderItem.objects.for_order(test_order1.id).total_items_cost()

        items = OrderItem.objects.filter(order=test_order1.id)
        expected_total = sum(
            item.price.amount * item.quantity for item in items
        )
        expected_money = Money(expected_total, settings.DEFAULT_CURRENCY)
        assert result == expected_money

    def test_total_items_cost_no_items(self):
        empty_order = OrderFactory.create(num_order_items=0)
        result = OrderItem.objects.for_order(empty_order.id).total_items_cost()

        expected_money = Money("0", settings.DEFAULT_CURRENCY)
        assert result == expected_money


@pytest.fixture
def test_manager_order():
    """Create test order for manager tests."""
    return OrderFactory.create(num_order_items=0)


@pytest.fixture
def test_manager_product():
    """Create test product for manager tests."""
    product = ProductFactory.create(
        stock=20, price=Money("15.00", settings.DEFAULT_CURRENCY)
    )
    product.set_current_language("en")
    product.name = "Test Product"
    product.save()
    return product


@pytest.fixture
def test_manager_items(test_manager_order, test_manager_product):
    """Create test items for manager tests."""
    items = []
    for _i in range(3):
        item = OrderItemFactory.create(
            order=test_manager_order,
            product=test_manager_product,
            price=Money("15.00", settings.DEFAULT_CURRENCY),
            quantity=2,
        )
        items.append(item)
    return items


@pytest.mark.django_db
class TestOrderItemManager:
    def test_for_order(self, test_manager_order, test_manager_items):
        result = OrderItem.objects.for_order(test_manager_order.id)

        for item in result:
            assert item.order == test_manager_order

        item_ids = [item.id for item in result]
        for item in test_manager_items:
            assert item.id in item_ids

    def test_for_product(self, test_manager_product, test_manager_items):
        result = OrderItem.objects.for_product(test_manager_product.id)

        for item in result:
            assert item.product == test_manager_product

        item_ids = [item.id for item in result]
        for item in test_manager_items:
            assert item.id in item_ids

    def test_with_product_data(self, test_manager_items):
        result = OrderItem.objects.with_product_data()

        assert result.count() > 0

        result_ids = [item.id for item in result]
        for item in test_manager_items:
            assert item.id in result_ids

    def test_sum_quantities(self, test_manager_order, test_manager_items):
        result = OrderItem.objects.for_order(
            test_manager_order.id
        ).sum_quantities()

        actual_items = OrderItem.objects.filter(order=test_manager_order)
        expected_quantity = sum(item.quantity for item in actual_items)

        assert result == expected_quantity
        assert expected_quantity > 0

    def test_total_items_cost(self, test_manager_order):
        result = OrderItem.objects.for_order(
            test_manager_order.id
        ).total_items_cost()

        actual_items = OrderItem.objects.filter(order=test_manager_order)
        expected_total = sum(
            item.price.amount * item.quantity for item in actual_items
        )
        expected_money = Money(expected_total, settings.DEFAULT_CURRENCY)

        assert result == expected_money
