import pytest
from django.core.exceptions import ValidationError
from django.test import TestCase as DjangoTestCase
from djmoney.money import Money

from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.models.item import OrderItem
from product.factories.product import ProductFactory


class OrderItemModelTestCase(DjangoTestCase):
    def setUp(self):
        self.order = OrderFactory.create()
        self.product = ProductFactory.create(
            stock=20, price=Money("50.00", "USD")
        )
        self.product.set_current_language("en")
        self.product.name = "Test Product"
        self.product.save()

        self.order_item = OrderItemFactory.create(
            order=self.order,
            product=self.product,
            quantity=3,
            refunded_quantity=0,
        )

    def test_str_representation(self):
        expected = f"Order {self.order.id} - {self.product.name} x {self.order_item.quantity}"
        self.assertEqual(str(self.order_item), expected)

    def test_get_ordering_queryset(self):
        queryset = self.order_item.get_ordering_queryset()
        self.assertIsNotNone(queryset)

    def test_clean_valid_quantity(self):
        self.order_item.clean()

    def test_clean_invalid_quantity(self):
        self.order_item.quantity = 0
        with self.assertRaises(ValidationError):
            self.order_item.clean()

    def test_clean_refunded_quantity_too_high(self):
        self.order_item.refunded_quantity = 5
        with self.assertRaises(ValidationError):
            self.order_item.clean()

    def test_clean_insufficient_stock(self):
        self.product.stock = 2
        self.product.save()

        new_item = OrderItem(
            order=self.order,
            product=self.product,
            price=self.product.price,
            quantity=5,
        )

        with self.assertRaises(ValidationError):
            new_item.clean()

    def test_clean_existing_item_stock_not_checked(self):
        self.order_item.save()
        self.product.stock = 0
        self.product.save()
        self.order_item.clean()

    def test_save_new_item(self):
        new_item = OrderItem(
            order=self.order,
            product=self.product,
            price=self.product.price,
            quantity=2,
        )
        new_item.save()

        new_item.refresh_from_db()

        self.assertEqual(new_item.original_quantity, 2)

    def test_save_existing_item(self):
        initial_stock = self.product.stock
        self.order_item.quantity = 5
        self.order_item.save()

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, initial_stock - 2)

    def test_total_price_property(self):
        expected_total = self.order_item.price * self.order_item.quantity
        self.assertEqual(self.order_item.total_price, expected_total)

    def test_net_quantity_property(self):
        self.order_item.refunded_quantity = 1
        expected_net = (
            self.order_item.quantity - self.order_item.refunded_quantity
        )
        self.assertEqual(self.order_item.net_quantity, expected_net)

    def test_net_price_property(self):
        self.order_item.refunded_quantity = 1
        expected_net_price = (
            self.order_item.price * self.order_item.net_quantity
        )
        self.assertEqual(self.order_item.net_price, expected_net_price)

    def test_refunded_amount_property_no_refund(self):
        expected = Money("0.00", "USD")
        self.assertEqual(self.order_item.refunded_amount, expected)

    def test_refunded_amount_property_partial_refund(self):
        self.order_item.refunded_quantity = 1
        self.order_item.save()
        expected = self.order_item.price * self.order_item.refunded_quantity
        self.assertEqual(self.order_item.refunded_amount, expected)

    def test_refund_invalid_quantity(self):
        self.order_item.quantity = 5
        self.order_item.refunded_quantity = 0

        with self.assertRaises(ValidationError):
            OrderItem.refund(self.order_item, -1)

        self.assertEqual(self.order_item.refunded_quantity, 0)

    def test_refund_too_much(self):
        self.order_item.quantity = 5
        self.order_item.refunded_quantity = 0

        with self.assertRaises(ValidationError):
            OrderItem.refund(self.order_item, 6)

        self.assertEqual(self.order_item.refunded_quantity, 0)

    def test_refund_partial(self):
        self.order_item.quantity = 5
        self.order_item.refunded_quantity = 0

        result = OrderItem.refund(self.order_item, 2)

        self.assertTrue(result)
        self.assertEqual(self.order_item.refunded_quantity, 2)

    def test_refund_full(self):
        self.order_item.quantity = 5
        self.order_item.refunded_quantity = 0

        result = OrderItem.refund(self.order_item, 5)

        self.assertTrue(result)
        self.assertEqual(self.order_item.refunded_quantity, 5)


@pytest.mark.django_db
class OrderItemQuerySetTestCase(DjangoTestCase):
    def setUp(self):
        OrderItem.objects.all().delete()

        self.order1 = OrderFactory.create(num_order_items=0)
        self.order2 = OrderFactory.create(num_order_items=0)

        self.product1 = ProductFactory.create(
            stock=50, price=Money("25.00", "USD")
        )
        self.product1.set_current_language("en")
        self.product1.name = "Product 1"
        self.product1.save()

        self.product2 = ProductFactory.create(
            stock=30, price=Money("40.00", "USD")
        )
        self.product2.set_current_language("en")
        self.product2.name = "Product 2"
        self.product2.save()

        self.item1 = OrderItemFactory.create(
            order=self.order1, product=self.product1, quantity=2
        )
        self.item2 = OrderItemFactory.create(
            order=self.order1, product=self.product2, quantity=3
        )
        self.item3 = OrderItemFactory.create(
            order=self.order2, product=self.product1, quantity=1
        )

    def test_for_order(self):
        result = OrderItem.objects.for_order(self.order1.id)
        items = list(result)

        item_ids = [item.id for item in items]
        self.assertIn(self.item1.id, item_ids)
        self.assertIn(self.item2.id, item_ids)

        for item in items:
            self.assertEqual(item.order_id, self.order1.id)

    def test_for_product(self):
        result = OrderItem.objects.for_product(self.product1.id)
        items = list(result)

        item_ids = [item.id for item in items]
        self.assertIn(self.item1.id, item_ids)
        self.assertIn(self.item3.id, item_ids)

        for item in items:
            self.assertEqual(item.product_id, self.product1.id)

    def test_with_product_data(self):
        result = OrderItem.objects.with_product_data()

        for item in result:
            self.assertIsNotNone(item.product.name)

    def test_sum_quantities(self):
        result = OrderItem.objects.for_order(self.order1.id).sum_quantities()

        items_for_order1 = OrderItem.objects.filter(order=self.order1.id)
        expected_quantity = sum(item.quantity for item in items_for_order1)
        self.assertEqual(result, expected_quantity)

        result2 = OrderItem.objects.for_order(self.order2.id).sum_quantities()
        items_for_order2 = OrderItem.objects.filter(order=self.order2.id)
        expected_quantity2 = sum(item.quantity for item in items_for_order2)
        self.assertEqual(result2, expected_quantity2)

        self.assertGreater(expected_quantity, 0)
        self.assertGreater(expected_quantity2, 0)

    def test_total_items_cost(self):
        result = OrderItem.objects.for_order(self.order1.id).total_items_cost()

        items = OrderItem.objects.filter(order=self.order1.id)
        expected_total = sum(
            item.price.amount * item.quantity for item in items
        )
        expected_money = Money(expected_total, "USD")
        self.assertEqual(result, expected_money)

    def test_total_items_cost_no_items(self):
        OrderItem.objects.all().delete()

        empty_order = OrderFactory.create(num_order_items=0)
        result = OrderItem.objects.for_order(empty_order.id).total_items_cost()

        expected_money = Money("0", "USD")
        self.assertEqual(result, expected_money)


@pytest.mark.django_db
class OrderItemManagerTestCase(DjangoTestCase):
    def setUp(self):
        OrderItem.objects.all().delete()

        self.order = OrderFactory.create(num_order_items=0)
        self.product = ProductFactory.create(
            stock=20, price=Money("15.00", "USD")
        )
        self.product.set_current_language("en")
        self.product.name = "Test Product"
        self.product.save()

        self.items = []
        for _i in range(3):
            item = OrderItemFactory.create(
                order=self.order,
                product=self.product,
                price=Money("15.00", "USD"),
                quantity=2,
            )
            self.items.append(item)

    def test_for_order(self):
        result = OrderItem.objects.for_order(self.order.id)

        for item in result:
            self.assertEqual(item.order, self.order)

        item_ids = [item.id for item in result]
        for item in self.items:
            self.assertIn(item.id, item_ids)

    def test_for_product(self):
        result = OrderItem.objects.for_product(self.product.id)

        for item in result:
            self.assertEqual(item.product, self.product)

        item_ids = [item.id for item in result]
        for item in self.items:
            self.assertIn(item.id, item_ids)

    def test_with_product_data(self):
        result = OrderItem.objects.with_product_data()

        self.assertGreater(result.count(), 0)

        result_ids = [item.id for item in result]
        for item in self.items:
            self.assertIn(item.id, result_ids)

    def test_sum_quantities(self):
        result = OrderItem.objects.for_order(self.order.id).sum_quantities()

        actual_items = OrderItem.objects.filter(order=self.order)
        expected_quantity = sum(item.quantity for item in actual_items)

        self.assertEqual(result, expected_quantity)
        self.assertGreater(expected_quantity, 0)

    def test_total_items_cost(self):
        result = OrderItem.objects.for_order(self.order.id).total_items_cost()

        actual_items = OrderItem.objects.filter(order=self.order)
        expected_total = sum(
            item.price.amount * item.quantity for item in actual_items
        )
        expected_money = Money(expected_total, "USD")

        self.assertEqual(result, expected_money)
