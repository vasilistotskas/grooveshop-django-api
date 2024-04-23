from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from djmoney.money import Money

from helpers.seed import get_or_create_default_image
from order.enum.status_enum import OrderStatusEnum
from order.models.order import Order
from pay_way.models import PayWay
from product.models.product import Product


class OrderModelTestCase(TestCase):
    order: Order = None
    product_1: Product = None
    product_2: Product = None

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
            status=OrderStatusEnum.PENDING,
            shipping_price=Decimal("10.00"),
        )

        self.product_1 = Product.objects.create(
            slug="product_one",
            price=10.00,
            active=True,
            stock=10,
            discount_percent=5.00,
            view_count=0,
            weight=0.00,
        )
        self.product_2 = Product.objects.create(
            slug="product_two",
            price=25.00,
            active=True,
            stock=10,
            discount_percent=10.00,
            view_count=0,
            weight=0.00,
        )

        self.order.order_item_order.create(
            product_id=self.product_1.id,
            price=Decimal("50.00"),
            quantity=2,
        )
        self.order.order_item_order.create(
            product_id=self.product_2.id,
            price=Decimal("30.00"),
            quantity=3,
        )

    def test_total_price_items_with_items(self):
        expected_total_price = 0
        for item in self.order.order_item_order.all():
            expected_total_price += item.total_price.amount

        self.assertEqual(self.order.total_price_items.amount, expected_total_price)

    def test_total_price_items_with_no_items(self):
        order = Order.objects.create(
            email="test@example.com",
            first_name="John",
            last_name="Doe",
            street="123 Main St",
            street_number="Apt 4",
            city="New York",
            zipcode="10001",
            phone="123-456-7890",
            status=OrderStatusEnum.PENDING,
            shipping_price=Decimal("10.00"),
        )

        self.assertEqual(order.total_price_items, Money("0", settings.DEFAULT_CURRENCY))

    def test_total_price_extra_with_pay_way(self):
        image_icon = get_or_create_default_image("uploads/pay_way/no_photo.jpg")
        pay_way = PayWay.objects.create(
            active=True,
            free_for_order_amount=Decimal("100.00"),
            cost=Decimal("5.00"),
            icon=image_icon,
        )
        order = Order.objects.create(
            email="test@example.com",
            first_name="John",
            last_name="Doe",
            street="123 Main St",
            street_number="Apt 4",
            city="New York",
            zipcode="10001",
            phone="123-456-7890",
            status=OrderStatusEnum.PENDING,
            shipping_price=Decimal("10.00"),
            pay_way=pay_way,
        )

        order.order_item_order.create(
            product_id=self.product_1.id,
            price=Decimal("50.00"),
            quantity=2,
        )
        expected_total_price_extra = Money("15.00", settings.DEFAULT_CURRENCY)
        self.assertEqual(order.total_price_extra, expected_total_price_extra)

        order.order_item_order.create(
            product_id=self.product_2.id,
            price=Decimal("50.00"),
            quantity=2,
        )
        expected_total_price_extra = Money("10.00", settings.DEFAULT_CURRENCY)
        self.assertEqual(order.total_price_extra, expected_total_price_extra)

    def test_total_price_extra_without_pay_way(self):
        order = Order.objects.create(
            email="test@example.com",
            first_name="John",
            last_name="Doe",
            street="123 Main St",
            street_number="Apt 4",
            city="New York",
            zipcode="10001",
            phone="123-456-7890",
            status=OrderStatusEnum.PENDING,
            shipping_price=Decimal("10.00"),
        )
        self.assertEqual(order.total_price_extra, order.shipping_price)

    def test_full_address(self):
        expected_full_address = (
            f"{self.order.street} {self.order.street_number}, "
            f"{self.order.zipcode} {self.order.city}"
        )
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
        self.assertEqual(self.order.status, OrderStatusEnum.PENDING)
        self.assertEqual(
            self.order.shipping_price, Money("10.00", settings.DEFAULT_CURRENCY)
        )
        self.assertEqual(self.order.paid_amount, Money("0", settings.DEFAULT_CURRENCY))

    def test_str_representation(self):
        self.assertEqual(str(self.order), f"Order {self.order.id} - John Doe")

    def tearDown(self) -> None:
        super().tearDown()
        self.order.delete()
        self.product_1.delete()
        self.product_2.delete()
