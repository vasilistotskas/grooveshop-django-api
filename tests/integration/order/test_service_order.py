from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from djmoney.money import Money

from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.services import OrderService
from product.factories.product import ProductFactory

User = get_user_model()


class OrderServiceTestCase(TestCase):
    def setUp(self):
        self.order = OrderFactory()
        self.user = self.order.user
        self.product = ProductFactory(stock=20)

        test_currency = self.order.shipping_price.currency

        self.order_data = {
            "email": "test@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "phone": "+1234567890",
            "paid_amount": Money(
                amount=Decimal("100.00"), currency=test_currency
            ),
            "status": OrderStatus.PENDING.value,
            "shipping_price": Money(
                amount=Decimal("10.00"), currency=test_currency
            ),
            "street": "123 Main St",
            "street_number": "Apt 4",
            "city": "Test City",
            "zipcode": "12345",
            "country": self.order.country,
            "region": self.order.region,
            "pay_way": self.order.pay_way,
        }
        self.items_data = [
            {
                "product": self.product,
                "quantity": 2,
            }
        ]

    def test_get_order_by_id(self):
        result = OrderService.get_order_by_id(self.order.id)

        self.assertEqual(result.id, self.order.id)

        result = OrderService.get_order_by_id(self.order.id)

        self.assertEqual(result.id, self.order.id)

    def test_get_order_by_uuid(self):
        result = OrderService.get_order_by_uuid(str(self.order.uuid))

        self.assertEqual(result.id, self.order.id)

        result = OrderService.get_order_by_uuid(str(self.order.uuid))

        self.assertEqual(result.id, self.order.id)

    @patch("order.signals.order_created.send")
    def test_create_order(self, mock_signal):
        initial_count = Order.objects.count()

        new_order = OrderService.create_order(
            order_data=self.order_data,
            items_data=self.items_data,
            user=self.user,
        )

        self.assertEqual(Order.objects.count(), initial_count + 1)

        self.assertEqual(new_order.email, self.order_data["email"])
        self.assertEqual(new_order.first_name, self.order_data["first_name"])

        self.assertEqual(new_order.items.count(), len(self.items_data))

        mock_signal.assert_called_once()

    def test_create_order_insufficient_stock(self):
        self.product.stock = 1
        self.product.save()

        with self.assertRaises(ValueError):
            OrderService.create_order(
                order_data=self.order_data,
                items_data=self.items_data,
                user=self.user,
            )

    def test_update_order_status_valid(self):
        with transaction.atomic():
            order = OrderFactory()
            order.status = OrderStatus.PENDING.value
            order.save(update_fields=["status"])

            order.refresh_from_db()
            self.assertEqual(order.status, OrderStatus.PENDING.value)

            updated_order = OrderService.update_order_status(
                order=order, new_status=OrderStatus.PROCESSING.value
            )

            self.assertEqual(updated_order.status, OrderStatus.PROCESSING.value)

            order.refresh_from_db()
            self.assertEqual(order.status, OrderStatus.PROCESSING.value)

            transaction.set_rollback(True)

    def test_update_order_status_invalid(self):
        with transaction.atomic():
            order = OrderFactory()
            order.status = OrderStatus.PENDING.value
            order.save(update_fields=["status"])

            order.refresh_from_db()
            self.assertEqual(order.status, OrderStatus.PENDING.value)

            with self.assertRaises(ValueError):
                OrderService.update_order_status(
                    order=order, new_status=OrderStatus.COMPLETED.value
                )

            order.refresh_from_db()
            self.assertEqual(order.status, OrderStatus.PENDING.value)

            transaction.set_rollback(True)

    def test_get_user_orders(self):
        with transaction.atomic():
            User = get_user_model()
            test_user = User.objects.create_user(
                username="testuser_orders",
                email="testuser_orders@example.com",
                password="password123",
            )

            order1 = OrderFactory.build(user=test_user)
            order1.save()
            order2 = OrderFactory.build(user=test_user)
            order2.save()

            other_user = User.objects.create_user(
                username="other_user",
                email="other_user@example.com",
                password="password123",
            )
            other_order = OrderFactory.build(user=other_user)
            other_order.save()

            self.assertEqual(Order.objects.filter(user=test_user).count(), 2)

            user_orders = OrderService.get_user_orders(test_user.id)

            self.assertEqual(user_orders.count(), 2)

            user_order_ids = [order.id for order in user_orders]
            self.assertIn(order1.id, user_order_ids)
            self.assertIn(order2.id, user_order_ids)

            self.assertNotIn(other_order.id, user_order_ids)

            transaction.set_rollback(True)

    @patch("order.signals.order_canceled.send")
    def test_cancel_order(self, mock_signal):
        order = OrderFactory()

        order.status = OrderStatus.PENDING.value
        order.save(update_fields=["status"])

        product = ProductFactory(stock=10)
        test_currency = order.shipping_price.currency
        order.items.create(
            product=product,
            price=Money(amount=Decimal("50.00"), currency=test_currency),
            quantity=3,
        )

        product.refresh_from_db()
        self.assertEqual(product.stock, 7)

        canceled_order = OrderService.cancel_order(order)

        self.assertEqual(canceled_order.status, OrderStatus.CANCELED.value)

        product.refresh_from_db()
        self.assertEqual(product.stock, 10)

    def test_calculate_shipping_cost(self):
        order_value = Money(amount=Decimal("49.99"), currency="USD")
        shipping_cost = OrderService.calculate_shipping_cost(order_value)
        self.assertTrue(shipping_cost.amount > 0)

        order_value = Money(amount=Decimal("500.00"), currency="USD")
        shipping_cost = OrderService.calculate_shipping_cost(order_value)
        self.assertEqual(shipping_cost.amount, 0)
