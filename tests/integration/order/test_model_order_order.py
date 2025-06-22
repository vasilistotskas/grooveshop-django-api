import logging
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from djmoney.money import Money

from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from product.factories.product import ProductFactory

logger = logging.getLogger(__name__)


class OrderModelTestCase(TestCase):
    def setUp(self):
        self.order = OrderFactory(
            pay_way=None,
            email="test@example.com",
            first_name="John",
            last_name="Doe",
            street="123 Main St",
            street_number="Apt 4",
            city="New York",
            zipcode="10001",
            phone="123-456-7890",
            status=OrderStatus.PENDING,
            shipping_price=Money("10.00", settings.DEFAULT_CURRENCY),
            paid_amount=Money("0", settings.DEFAULT_CURRENCY),
        )

        products = ProductFactory.create_batch(2, num_images=0, num_reviews=0)

        self.product_1 = products[0]
        self.product_2 = products[1]

        self.order.items.create(
            product_id=self.product_1.id,
            price=Decimal("50.00"),
            quantity=2,
        )
        self.order.items.create(
            product_id=self.product_2.id,
            price=Decimal("30.00"),
            quantity=3,
        )

    def test_total_price_items_with_items(self):
        expected_total_price = sum(
            item.total_price.amount for item in self.order.items.all()
        )
        self.assertEqual(
            self.order.total_price_items.amount, expected_total_price
        )

    def test_total_price_extra_without_pay_way(self):
        self.assertEqual(
            self.order.total_price_extra, self.order.shipping_price
        )

    def test_full_address(self):
        expected_full_address = f"{self.order.street} {self.order.street_number}, {self.order.zipcode} {self.order.city}"
        self.assertEqual(self.order.full_address, expected_full_address)

    def test_fields(self):
        self.assertEqual(self.order.email, "test@example.com")
        self.assertEqual(self.order.first_name, "John")
        self.assertEqual(self.order.last_name, "Doe")
        self.assertEqual(self.order.street, "123 Main St")
        self.assertEqual(self.order.street_number, "Apt 4")
        self.assertEqual(self.order.city, "New York")
        self.assertEqual(self.order.zipcode, "10001")
        self.assertEqual(self.order.phone, "123-456-7890")
        self.assertEqual(self.order.status, OrderStatus.PENDING)
        self.assertEqual(
            self.order.shipping_price,
            Money("10.00", settings.DEFAULT_CURRENCY),
        )
        self.assertEqual(
            self.order.paid_amount, Money("0", settings.DEFAULT_CURRENCY)
        )

    def test_str_representation(self):
        self.assertEqual(str(self.order), f"Order {self.order.id} - John Doe")
