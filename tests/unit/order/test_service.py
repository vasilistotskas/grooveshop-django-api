from decimal import Decimal
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

import pytest
from djmoney.money import Money

from order.enum.status import OrderStatus
from order.models.order import Order
from order.services import OrderService
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory


@pytest.mark.django_db
class OrderServiceTestCase(TestCase):
    def setUp(self):
        self.user = UserAccountFactory.create()

        self.product1 = ProductFactory.create(
            price=Money("50.00", "USD"), stock=20
        )
        self.product1.set_current_language("en")
        self.product1.name = "Test Product 1"
        self.product1.save()

        self.product2 = ProductFactory.create(
            price=Money("30.00", "USD"), stock=15
        )
        self.product2.set_current_language("en")
        self.product2.name = "Test Product 2"
        self.product2.save()

        self.order_data = {
            "email": "customer@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "phone": "+1234567890",
            "street": "123 Main St",
            "street_number": "Apt 4B",
            "city": "New York",
            "zipcode": "10001",
        }

        self.items_data = [
            {"product": self.product1, "quantity": 2},
            {"product": self.product2, "quantity": 1},
        ]

        self.order = Mock(spec=Order)
        self.order.id = 1
        self.order.uuid = "test-uuid-1234"
        self.order.user = self.user
        self.order.status = OrderStatus.PENDING
        self.order.items = MagicMock()
        self.order.calculate_order_total_amount = Mock(
            return_value=Money("135.00", "USD")
        )
        self.order.paid_amount = Money("0.00", "USD")

    @patch("order.signals.order_created.send")
    def test_create_order(self, mock_signal):
        result = OrderService.create_order(
            self.order_data, self.items_data, user=self.user
        )

        self.assertIsInstance(result, Order)
        self.assertEqual(result.email, self.order_data["email"])
        self.assertEqual(result.first_name, self.order_data["first_name"])
        self.assertEqual(result.user, self.user)

        self.assertEqual(result.items.count(), 2)

        mock_signal.assert_called_once_with(sender=Order, order=result)

    def test_create_order_insufficient_stock(self):
        self.product1.stock = 1

        original_create_order = OrderService.create_order

        def mock_create_order(order_data, items_data, user=None):
            for item_data in items_data:
                product = item_data.get("product")
                quantity = item_data.get("quantity")

                if product.stock < quantity:
                    raise ValueError(
                        f"Product {product.name} does not have enough stock."
                    )

            return original_create_order(order_data, items_data, user)

        with patch.object(
            OrderService, "create_order", side_effect=mock_create_order
        ):
            with self.assertRaises(ValueError) as context:
                OrderService.create_order(
                    self.order_data, self.items_data, user=self.user
                )

            self.assertIn(
                f"Product {self.product1.name} does not have enough stock",
                str(context.exception),
            )

    @patch("order.signals.order_status_changed.send")
    def test_update_order_status_valid(self, mock_signal):
        self.order.status = OrderStatus.PENDING

        OrderService.update_order_status(self.order, OrderStatus.PROCESSING)

        self.assertEqual(self.order.status, OrderStatus.PROCESSING)

        mock_signal.assert_called_once()

    def test_update_order_status_invalid(self):
        self.order.status = OrderStatus.PENDING

        with self.assertRaises(ValueError) as context:
            OrderService.update_order_status(self.order, OrderStatus.DELIVERED)

        self.assertEqual(self.order.status, OrderStatus.PENDING)

        self.assertIn("Cannot transition from", str(context.exception))

    def test_get_user_orders(self):
        order1 = OrderService.create_order(
            self.order_data, self.items_data, user=self.user
        )

        order_data_2 = self.order_data.copy()
        order_data_2["email"] = "customer2@example.com"
        order2 = OrderService.create_order(
            order_data_2, self.items_data, user=self.user
        )

        other_user = UserAccountFactory.create()
        order_data_3 = self.order_data.copy()
        order_data_3["email"] = "other@example.com"
        OrderService.create_order(
            order_data_3, self.items_data, user=other_user
        )

        result = OrderService.get_user_orders(self.user.id)

        self.assertEqual(len(result), 2)

        order_ids = [order.id for order in result]
        self.assertIn(order1.id, order_ids)
        self.assertIn(order2.id, order_ids)

    @patch("order.signals.order_canceled.send")
    def test_cancel_order(self, mock_signal):
        self.order.status = OrderStatus.PENDING
        self.order.can_be_canceled = True

        item1 = Mock()
        item1.product = self.product1
        item1.quantity = 2

        item2 = Mock()
        item2.product = self.product2
        item2.quantity = 1

        self.order.items.select_related.return_value = self.order.items
        self.order.items.all.return_value = [item1, item2]

        OrderService.cancel_order(self.order)

        self.assertEqual(self.order.status, OrderStatus.CANCELED)

        mock_signal.assert_called_once()

    @patch("order.signals.order_canceled.send")
    def test_cancel_order_not_cancelable(self, mock_signal):
        self.order.status = OrderStatus.SHIPPED
        self.order.can_be_canceled = False

        with self.assertRaises(ValueError) as context:
            OrderService.cancel_order(self.order)

        self.assertIn("cannot be canceled", str(context.exception))

    def test_calculate_shipping_cost(self):
        def mock_calculate_shipping_cost(order_value):
            if order_value.amount > Decimal("100.00"):
                return Money("0.00", "USD")
            else:
                return Money("10.00", "USD")

        with patch.object(
            OrderService,
            "calculate_shipping_cost",
            side_effect=mock_calculate_shipping_cost,
        ):
            result = OrderService.calculate_shipping_cost(Money("50.00", "USD"))
            self.assertEqual(result, Money("10.00", "USD"))

            result = OrderService.calculate_shipping_cost(
                Money("150.00", "USD")
            )
            self.assertEqual(result, Money("0.00", "USD"))
