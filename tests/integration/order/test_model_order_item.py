from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from djmoney.money import Money

from order.factories.order import OrderFactory
from product.factories.product import ProductFactory


class OrderItemModelTestCase(TestCase):
    def setUp(self):
        self.order = OrderFactory()
        self.product = ProductFactory(num_images=0, num_reviews=0)
        self.order_item = self.order.items.create(
            product=self.product, price=Decimal("20.00"), quantity=3
        )

    def test_total_price(self):
        expected_total_price = self.order_item.price * self.order_item.quantity
        self.assertEqual(self.order_item.total_price, expected_total_price)

    def test_fields(self):
        self.assertEqual(self.order_item.order, self.order)
        self.assertEqual(self.order_item.product, self.product)
        self.assertEqual(
            self.order_item.price, Money("20.00", settings.DEFAULT_CURRENCY)
        )
        self.assertEqual(self.order_item.quantity, 3)
