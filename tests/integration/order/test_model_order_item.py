from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from djmoney.money import Money

from order.models.item import OrderItem
from order.models.order import Order
from product.models.product import Product


class OrderItemModelTestCase(TestCase):
    order: Order = None
    product: Product = None
    order_item: OrderItem = None

    def setUp(self):
        self.order = Order.objects.create(
            email="test@example.com",
            first_name="John",
            last_name="Doe",
            street="123 Main St",
            street_number="Apt 4",
            city="New York",
            zipcode="10001",
            phone="123-456-7890",
            paid_amount=Decimal("150.00"),
            status="Pending",
            shipping_price=Decimal("10.00"),
        )
        self.product = Product.objects.create(
            slug="test-product",
            price=Decimal("20.00"),
            active=True,
            stock=10,
            discount_percent=Decimal("5.00"),
            view_count=0,
            weight=0.00,
        )
        self.order_item = self.order.order_item_order.create(
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

    def tearDown(self) -> None:
        super().tearDown()
        self.order_item.delete()
        self.order.delete()
        self.product.delete()
