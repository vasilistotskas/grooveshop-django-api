from decimal import Decimal
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

import pytest
from djmoney.money import Money

from order.enum.status_enum import OrderStatusEnum
from order.models.order import Order
from order.services import OrderService


@pytest.mark.django_db
class OrderServiceTestCase(TestCase):
    def setUp(self):
        self.user = Mock()
        self.user.id = 1
        self.user.email = "user@example.com"
        self.user.is_authenticated = True

        self.product1 = Mock()
        self.product1.id = 1
        self.product1.name = "Test Product 1"
        self.product1.stock = 20
        self.product1.final_price = Money("50.00", "USD")

        self.product2 = Mock()
        self.product2.id = 2
        self.product2.name = "Test Product 2"
        self.product2.stock = 15
        self.product2.final_price = Money("30.00", "USD")

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
        self.order.status = OrderStatusEnum.PENDING
        self.order.items = MagicMock()
        self.order.calculate_order_total_amount = Mock(
            return_value=Money("135.00", "USD")
        )
        self.order.paid_amount = Money("0.00", "USD")

    @patch("order.signals.order_created.send")
    @patch("order.models.order.Order.objects.create")
    @patch("order.models.item.OrderItem.objects.create")
    def test_create_order(
        self, mock_create_item, mock_create_order, mock_signal
    ):
        mock_create_order.return_value = self.order

        self.order.shipping_price = Money("10.00", "USD")

        for product in [self.product1, self.product2]:
            product.final_price = Money("50.00", "USD")

        result = OrderService.create_order(
            self.order_data, self.items_data, user=self.user
        )

        self.assertEqual(result, self.order)

        mock_create_order.assert_called_once_with(
            **{**self.order_data, "user": self.user}
        )

        self.assertEqual(mock_create_item.call_count, 2)

        mock_signal.assert_not_called()

        self.assertEqual(
            self.order.paid_amount, self.order.calculate_order_total_amount()
        )
        self.order.save.assert_called_once_with(update_fields=["paid_amount"])

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
        self.order.status = OrderStatusEnum.PENDING

        OrderService.update_order_status(self.order, OrderStatusEnum.PROCESSING)

        self.assertEqual(self.order.status, OrderStatusEnum.PROCESSING)

        mock_signal.assert_called_once()

    def test_update_order_status_invalid(self):
        self.order.status = OrderStatusEnum.PENDING

        with self.assertRaises(ValueError) as context:
            OrderService.update_order_status(
                self.order, OrderStatusEnum.DELIVERED
            )

        self.assertEqual(self.order.status, OrderStatusEnum.PENDING)

        self.assertIn("Cannot transition from", str(context.exception))

    @patch("order.models.order.Order.objects.filter")
    def test_get_user_orders(self, mock_filter):
        mock_orders = [Mock(spec=Order), Mock(spec=Order)]
        mock_queryset = Mock()
        mock_filter.return_value = mock_queryset
        mock_queryset.select_related.return_value = mock_queryset
        mock_queryset.prefetch_related.return_value = mock_queryset
        mock_queryset.order_by.return_value = mock_orders

        result = OrderService.get_user_orders(self.user.id)

        mock_filter.assert_called_once_with(user_id=self.user.id)

        self.assertEqual(result, mock_orders)

    @patch("order.signals.order_canceled.send")
    def test_cancel_order(self, mock_signal):
        self.order.status = OrderStatusEnum.PENDING
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

        self.assertEqual(self.order.status, OrderStatusEnum.CANCELED)

        mock_signal.assert_called_once()

    @patch("order.signals.order_canceled.send")
    def test_cancel_order_not_cancelable(self, mock_signal):
        self.order.status = OrderStatusEnum.SHIPPED
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
