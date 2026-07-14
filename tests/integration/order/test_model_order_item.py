from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
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

    def test_db_rejects_over_refund(self):
        """The DB CheckConstraint refuses refunded_quantity > quantity
        (G0247)."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.order.items.create(
                    product=ProductFactory(num_images=0, num_reviews=0),
                    price=Decimal("20.00"),
                    quantity=2,
                    refunded_quantity=3,
                )

    def test_db_rejects_zero_quantity(self):
        """The DB CheckConstraint refuses quantity < 1 (G0247)."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.order.items.create(
                    product=ProductFactory(num_images=0, num_reviews=0),
                    price=Decimal("20.00"),
                    quantity=0,
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
