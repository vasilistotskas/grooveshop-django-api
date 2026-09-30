from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from djmoney.money import Money

from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from product.factories.product import ProductFactory


class OrderModelTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.order = OrderFactory(
            pay_way=None,
            first_name="John",
            last_name="Doe",
            street="Main St",
            street_number="4",
            city="New York",
            zipcode="10001",
            status=OrderStatus.PENDING,
            shipping_price=Money("10.00", settings.DEFAULT_CURRENCY),
            num_order_items=0,
        )
        first, second = ProductFactory.create_batch(
            2, num_images=0, num_reviews=0
        )
        cls.order.items.create(
            product=first, price=Decimal("50.00"), quantity=2
        )
        cls.order.items.create(
            product=second, price=Decimal("30.00"), quantity=3
        )

    def test_total_price_items_sums_the_lines(self):
        self.assertEqual(
            self.order.total_price_items,
            Money("190.00", settings.DEFAULT_CURRENCY),
        )

    def test_total_price_extra_is_shipping_without_a_pay_way(self):
        self.assertEqual(
            self.order.total_price_extra,
            Money("10.00", settings.DEFAULT_CURRENCY),
        )

    def test_full_address(self):
        self.assertEqual(self.order.full_address, "Main St 4, 10001 New York")

    def test_str_representation(self):
        self.assertEqual(str(self.order), f"Order {self.order.id} - John Doe")
