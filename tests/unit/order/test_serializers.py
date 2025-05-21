from django.contrib.auth import get_user_model
from django.test import TestCase
from djmoney.money import Money

from country.factories import CountryFactory
from order.enum.status_enum import OrderStatusEnum
from order.factories.order import OrderFactory
from order.serializers.item import OrderItemSerializer
from order.serializers.order import (
    OrderCreateUpdateSerializer,
    OrderDetailSerializer,
    OrderSerializer,
)
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from region.factories import RegionFactory

User = get_user_model()


class OrderSerializerTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="test1@example.com",
            password="testpassword",
        )

        self.pay_way = PayWayFactory()
        self.country = CountryFactory()
        self.region = RegionFactory(country=self.country)

        self.order = OrderFactory(
            user=self.user,
            pay_way=self.pay_way,
            country=self.country,
            region=self.region,
            status=OrderStatusEnum.PENDING.value,
            paid_amount=Money("100.00", "USD"),
            shipping_price=Money("10.00", "USD"),
        )

        product = ProductFactory()
        self.order_item = self.order.items.create(
            product=product, price=Money("50.00", "USD"), quantity=2
        )

        self.serializer = OrderSerializer(instance=self.order)


class OrderDetailSerializerTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="test2@example.com",
            password="testpassword",
        )

        self.pay_way = PayWayFactory()
        self.country = CountryFactory()
        self.region = RegionFactory(country=self.country)

        self.order = OrderFactory(
            user=self.user,
            pay_way=self.pay_way,
            country=self.country,
            region=self.region,
            status=OrderStatusEnum.PENDING.value,
        )

        product = ProductFactory(
            name="Test Product", price=Money("50.00", "USD")
        )
        self.order_item = self.order.items.create(
            product=product, price=Money("50.00", "USD"), quantity=2
        )

        self.serializer = OrderDetailSerializer(instance=self.order)

    def test_contains_expected_fields(self):
        data = self.serializer.data

        expected_additional_fields = {
            "items",
            "tracking_number",
            "shipping_carrier",
        }

        self.assertTrue(
            all(field in data for field in expected_additional_fields)
        )


class OrderCreateUpdateSerializerTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="test3@example.com",
            password="testpassword",
        )

        self.pay_way = PayWayFactory()
        self.country = CountryFactory()
        self.region = RegionFactory(country=self.country)

        self.product = ProductFactory(stock=10, price=Money("50.00", "USD"))

        self.valid_data = {
            "email": "customer@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "phone": "+12025550195",
            "paid_amount": {"amount": "100.00", "currency": "USD"},
            "status": OrderStatusEnum.PENDING.value,
            "shipping_price": {"amount": "10.00", "currency": "USD"},
            "street": "Main Street",
            "street_number": "123",
            "city": "Testville",
            "zipcode": "12345",
            "country": self.country.alpha_2,
            "region": self.region.alpha,
            "pay_way": self.pay_way.id,
            "items": [
                {
                    "product": self.product.id,
                    "quantity": 2,
                    "price": {"amount": "50.00", "currency": "USD"},
                }
            ],
        }

    def test_items_validation(self):
        invalid_data = self.valid_data.copy()
        invalid_data["items"] = []

        serializer = OrderCreateUpdateSerializer(data=invalid_data)
        self.assertFalse(serializer.is_valid())

        self.assertFalse(serializer.is_valid())

    def test_money_field_validation(self):
        invalid_data = self.valid_data.copy()
        invalid_data["paid_amount"] = {
            "amount": "-50.00",
            "currency": "USD",
        }

        serializer = OrderCreateUpdateSerializer(data=invalid_data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("paid_amount", serializer.errors)

        invalid_data = self.valid_data.copy()
        invalid_data["paid_amount"] = {
            "amount": "50.00",
            "currency": "INVALID",
        }

        serializer = OrderCreateUpdateSerializer(data=invalid_data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("paid_amount", serializer.errors)

    def test_phone_number_validation(self):
        invalid_data = self.valid_data.copy()
        invalid_data["phone"] = "not-a-phone-number"

        serializer = OrderCreateUpdateSerializer(data=invalid_data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("phone", serializer.errors)

    def test_required_fields(self):
        invalid_data = self.valid_data.copy()
        del invalid_data["email"]

        serializer = OrderCreateUpdateSerializer(data=invalid_data)
        serializer.is_valid()
        self.assertIn("email", serializer.errors)

        invalid_data = self.valid_data.copy()
        del invalid_data["first_name"]

        serializer = OrderCreateUpdateSerializer(data=invalid_data)
        serializer.is_valid()
        self.assertIn("first_name", serializer.errors)

    def test_email_validation(self):
        invalid_data = self.valid_data.copy()
        invalid_data["email"] = "not-an-email"

        serializer = OrderCreateUpdateSerializer(data=invalid_data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("email", serializer.errors)


class OrderItemSerializerTestCase(TestCase):
    def setUp(self):
        self.product = ProductFactory(
            name="Test Product", price=Money("50.00", "USD")
        )
        self.order = OrderFactory()
        self.order_item = self.order.items.create(
            product=self.product, price=Money("50.00", "USD"), quantity=2
        )

        self.serializer = OrderItemSerializer(instance=self.order_item)

    def test_contains_expected_fields(self):
        data = self.serializer.data

        expected_fields = {
            "id",
            "price",
            "product",
            "quantity",
            "original_quantity",
            "is_refunded",
            "refunded_quantity",
            "net_quantity",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
            "total_price",
            "refunded_amount",
            "net_price",
            "notes",
        }

        self.assertTrue(all(field in data for field in expected_fields))
